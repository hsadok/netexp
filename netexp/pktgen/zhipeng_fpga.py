
from netexp.helpers import RemoteIntelFpga, remote_command, upload_file, \
    watch_command
from netexp.pktgen import Pktgen

import math
import sys

from pathlib import Path


PARSE_OUTPUT_CMD = 'rtl_sim/input_gen/run.sh'
INPUT_GEN_CMD = 'hardware_test/hwtest/input_gen.py'
PCAP_GEN_CMD = 'frontend/generate_synthetic_trace.py'
PCAP_DEST = Path('hardware_test/hwtest')

MAX_NB_PCAP_FLITS = 16384


class FpgaPktgen(Pktgen):
    def __init__(self, server, fpga_id, remote_dir, load_bitstream=True):
        self._rate = 0
        self._nb_flits_in_pcap = 0
        self._rx_window = 0
        self._tx_window = 0
        self._nb_pcap_iters = 0  # 0 means forever
        self.fpga = RemoteIntelFpga(server, fpga_id, remote_dir,
                                    load_bitstream=load_bitstream)

        # make sure it is not running and registers are clear
        if not load_bitstream:
            self.stop()
            self.clear()

    def run_and_watch_cmd(self, cmd, dir=None):
        app = remote_command(self.ssh_client, cmd, pty=True, dir=dir)
        watch_command(app, keyboard_int=lambda: app.send('\x03'))
        status = app.recv_exit_status()
        return status

    def set_params(self, pkt_size, nb_src, nb_dst):
        nb_pkts = nb_src * nb_dst

        pkt_size_in_flits = int(math.ceil(pkt_size / 64))
        total_nb_flits = nb_pkts * pkt_size_in_flits

        if total_nb_flits > MAX_NB_PCAP_FLITS:
            sys.stderr.write('pcap size exceeds maximum number of flits')
            raise RuntimeError()

        remote_dir_path = Path(self.fpga.remote_dir)
        pcap_dst = remote_dir_path / PCAP_DEST
        pcap_gen_cmd = remote_dir_path / Path(PCAP_GEN_CMD)
        pcap_gen_cmd = f'{pcap_gen_cmd} {nb_pkts} {pkt_size} {nb_src} {nb_dst}'
        self.run_and_watch_cmd(pcap_gen_cmd, dir=pcap_dst)

        out_pcap = '_'.join(str(x) for x in (nb_pkts, pkt_size, nb_src, nb_dst))
        out_pcap += '.pcap'

        self.parse_pcap(out_pcap)
        self.reload_pcap()

    def parse_pcap(self, pcap_name):
        pcap_name = Path(pcap_name)
        remote_dir_path = Path(self.fpga.remote_dir)
        pcap_dst = remote_dir_path / PCAP_DEST
        pkt_dest = pcap_dst / pcap_name.with_suffix('.pkt').name

        parse_cmd = remote_dir_path / Path(PARSE_OUTPUT_CMD)
        parse_cmd = f'{parse_cmd} {pcap_dst / pcap_name} {pkt_dest}'

        in_gen_cmd = remote_dir_path / Path(INPUT_GEN_CMD)
        in_gen_cmd = f'{in_gen_cmd} {pkt_dest}'

        self.run_and_watch_cmd(parse_cmd, dir=pcap_dst)
        self.run_and_watch_cmd(in_gen_cmd, dir=pcap_dst)

    def load_pcap(self, pcap_file_name, local=True):
        """Load local pcap in remote FPGA."""
        pcap_path = Path(pcap_file_name)
        remote_dir_path = Path(self.fpga.remote_dir)
        pcap_dst = remote_dir_path / PCAP_DEST

        if local:
            upload_file(pcap_path, self.fpga.server, pcap_dst)
        else:
            cmd = f'cp {pcap_path} {pcap_dst}'
            if self.run_and_watch_cmd(cmd, dir=pcap_dst):
                raise RuntimeError()

        self.parse_pcap(pcap_path.name)
        self.reload_pcap()

    def reload_pcap(self):
        """Reload last loaded pcap."""
        remote_dir_path = Path(self.fpga.remote_dir)
        pcap_dst = remote_dir_path / PCAP_DEST
        app = remote_command(self.fpga.ssh_client, 'wc -l < meta.txt', pty=True,
                             dir=pcap_dst)
        output = watch_command(app, keyboard_int=lambda: app.send('\x03'))
        nb_flits = int(output)
        print(f'pcap has {nb_flits} flits')

        self.nb_flits_in_pcap = nb_flits
        self.load_mem()
        self.load_meta()

        self.clear()

    @property
    def rate(self):
        return self._rate

    @rate.setter
    def rate(self, rate):
        self._rate = rate
        self.fpga.run_jtag_commands(f'set_rate {rate}')

    def load_mem(self):
        self.fpga.run_jtag_commands('load_mem')

    def load_meta(self):
        self.fpga.run_jtag_commands('load_meta')

    @property
    def nb_flits_in_pcap(self):
        return self._nb_flits_in_pcap

    @nb_flits_in_pcap.setter
    def nb_flits_in_pcap(self, nb_flits_in_pcap):
        self._nb_flits_in_pcap = nb_flits_in_pcap
        self.fpga.run_jtag_commands(f'set_flit_size {nb_flits_in_pcap}')

    @property
    def rx_window(self):
        return self._rx_window

    @rx_window.setter
    def rx_window(self, rx_window):
        self._rx_window = rx_window
        self.fpga.run_jtag_commands(f'set_rx_pkt_win {rx_window}')

    @property
    def tx_window(self):
        return self._tx_window

    @tx_window.setter
    def tx_window(self, tx_window):
        self._tx_window = tx_window
        self.fpga.run_jtag_commands(f'set_tx_pkt_win {tx_window}')

    @property
    def nb_pcap_iters(self):
        return self._nb_pcap_iters

    @nb_pcap_iters.setter
    def nb_pcap_iters(self, nb_pcap_iters):
        self._nb_pcap_iters = nb_pcap_iters
        self.fpga.run_jtag_commands(f'set_pkt_num {nb_pcap_iters}')

    def get_top_stats(self):
        self.fpga.jtag_console.send('get_top_stats\n')
        output = watch_command(
            self.fpga.jtag_console,
            keyboard_int=lambda: self.fpga.jtag_console.send('\x03'),
            stop_pattern='REG_OUT_PKT_23_FLIT'
        )

        lines = output.split('\n')

        stats = {}
        for line in lines:
            if ':' in line:
                key, value = line.split(':')
                if key.startswith('\x1b[?1l\x1b>\x1b[?2004l'):
                    key = key.split('\x1b[?1l\x1b>\x1b[?2004l')[1]
                stats[key] = int(value)

        return stats

    def _get_single_xput(self, xput_type):
        self.fpga.jtag_console.send(f'get_{xput_type}_xput\n')
        output = watch_command(
            self.fpga.jtag_console,
            keyboard_int=lambda: self.fpga.jtag_console.send('\x03'),
            stop_pattern='\r\n% '
        )

        lines = output.split('\n')
        value = 0.0

        for line in lines:
            if 'Gbps' in line:
                if line.startswith('\x1b[?1l\x1b>\x1b[?2004l'):
                    line = line.split('\x1b[?1l\x1b>\x1b[?2004l')[1]
                value = float(line.split('Gbps')[0])
                break

        return value

    def get_tx_xput(self):
        return self._get_single_xput('tx')

    def get_rx_xput(self):
        return self._get_single_xput('rx')

    def start(self):
        self.fpga.run_jtag_commands('start')

    def stop(self):
        self.fpga.run_jtag_commands('stop')

    def clear(self):
        self.fpga.run_jtag_commands('set_clear')

        # need to reset config after clear
        self.rate = self._rate
        self.nb_flits_in_pcap = self._nb_flits_in_pcap
        self.rx_window = self._rx_window
        self.tx_window = self._tx_window
        self.nb_pcap_iters = self._nb_pcap_iters
