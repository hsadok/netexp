
import ipaddress
import time

from typing import Any, Optional, Iterable

from netexp.pktgen import Pktgen
from netexp.helpers import get_ssh_client, posix_shell, remote_command, \
    run_console_commands, watch_command


class DpdkConfig:
    """Represents DPDK command-line options.
    """
    def __init__(self, cores: Iterable[int], mem_channels: int, drivers=None,
                 mem_alloc=None, mem_ranks=None, xen_dom0=False, syslog=False,
                 socket_mem=None, huge_dir=None, proc_type=None,
                 file_prefix=None, pci_block_list=None, pci_allow_list=None,
                 vdev=None, vmware_tsc_map=False, base_virtaddr=False,
                 vfio_intr=None, create_uio_dev=None, extra_opt: str = None):

        self.cores = cores
        self.mem_channels = mem_channels
        self.drivers = drivers
        self.mem_alloc = mem_alloc
        self.mem_ranks = mem_ranks
        self.xen_dom0 = xen_dom0
        self.syslog = syslog
        self.socket_mem = socket_mem
        self.huge_dir = huge_dir
        self.proc_type = proc_type
        self.file_prefix = file_prefix
        self.pci_block_list = pci_block_list
        self.pci_allow_list = pci_allow_list
        self.vdev = vdev
        self.vmware_tsc_map = vmware_tsc_map
        self.base_virtaddr = base_virtaddr
        self.vfio_intr = vfio_intr
        self.create_uio_dev = create_uio_dev
        self.extra_opt = extra_opt

        if drivers is not None and not isinstance(drivers, list):
            self.drivers = [self.drivers]

        if pci_allow_list is not None and not isinstance(pci_allow_list, list):
            self.pci_allow_list = [self.pci_allow_list]

        if pci_block_list is not None and not isinstance(pci_block_list, list):
            self.pci_block_list = [self.pci_block_list]

    def __str__(self) -> str:
        opts = '-l ' + ','.join(str(c) for c in self.cores)
        opts += f' -n {self.mem_channels}'

        if self.drivers is not None:
            for driver in self.drivers:
                opts += f' -d {driver}'

        if self.mem_alloc is not None:
            opts += f' -m {self.mem_alloc}'

        if self.mem_ranks is not None:
            opts += f' -r {self.mem_ranks}'

        if self.xen_dom0:
            opts += ' --xen-dom0'

        if self.syslog:
            opts += ' --syslog'

        if self.socket_mem is not None:
            opts += f' --socket-mem {self.socket_mem}'

        if self.huge_dir is not None:
            opts += f' --huge-dir {self.huge_dir}'

        if self.proc_type is not None:
            opts += f' --proc-type {self.proc_type}'

        if self.file_prefix is not None:
            opts += f' --file-prefix {self.file_prefix}'

        if self.pci_block_list is not None:
            for pci_block_list in self.pci_block_list:
                opts += f' -b {pci_block_list}'

        if self.pci_allow_list is not None:
            for pci_allow_list in self.pci_allow_list:
                opts += f' -a {pci_allow_list}'

        if self.vdev is not None:
            opts += f' --vdev {self.vdev}'

        if self.vmware_tsc_map:
            opts += ' --vmware-tsc-map'

        if self.base_virtaddr:
            opts += f' --base-virt-addr {self.base_virtaddr}'

        if self.vfio_intr is not None:
            opts += f' --vfio-intr {self.vfio_intr}'

        if self.create_uio_dev:
            opts += ' --create-uio-dev'

        if self.extra_opt is not None:
            opts += self.extra_opt

        return opts


class DpdkPktgen(Pktgen):
    """Wrapper for DPDK pktgen.

    It assumes that DPDK pktgen can be executed remotely by running `pktgen`.
    It also requires that DPDK pktgen be built with Lua support, e.g.,
    `meson build -Denable_lua=true`

    Attributes:
        pktgen_server: The remote to run pktgen on.
        dpdk_config: CLI config to pass to DPDK. Can use either `DpdkConfig` or
          `str`.
        port_map:
        port:
        pcap:
        config_file:
        log_file:
        promiscuous:
        numa_support:
        extra_opt:
    """
    def __init__(self, pktgen_server: str, dpdk_config, port_map: str,
                 max_throughput: float, port: int = 0,
                 pcap: Optional[str] = None, config_file: Optional[str] = None,
                 log_file: Optional[str] = None, promiscuous=False,
                 numa_support=False, extra_opt: Optional[str] = None):
        self.pktgen_ssh_client = get_ssh_client(pktgen_server)
        pktgen_options = f'-m "{port_map}"'
        self.max_throughput = max_throughput
        self.port = port

        if pcap is not None:
            pktgen_options += f' -s 0:{pcap}'
            self.use_pcap = True
        else:
            self.use_pcap = False

        if config_file is not None:
            pktgen_options += f' -f {config_file}'

        if log_file is not None:
            pktgen_options += f' -l {log_file}'

        if promiscuous:
            pktgen_options += ' -P'

        if numa_support:
            pktgen_options += ' -N'

        if extra_opt is not None:
            pktgen_options += extra_opt

        remote_cmd = f'sudo pktgen {dpdk_config} -- {pktgen_options}'
        self.pktgen = remote_command(self.pktgen_ssh_client, remote_cmd,
                                     pty=True, print_command=True)
        self.remote_cmd = remote_cmd
        self.target_pkt_tx = 0

        self.ready = False

    def wait_ready(self, stdout: bool = True, stderr: bool = True) -> None:
        watch_command(self.pktgen, keyboard_int=self.pktgen.close,
                      stop_pattern='Pktgen:/>', stdout=stdout, stderr=stderr)
        self.ready = True

    def commands(self, cmds, timeout: float = 0.5) -> None:
        run_console_commands(self.pktgen, cmds, timeout=timeout,
                             console_pattern='\r\nPktgen:/> ')

    def set_params(self, pkt_size: int, nb_src: int, nb_dst: int,
                   init_ip: Any = None, init_port: int = 0,
                   port: Optional[int] = None) -> None:
        init_ip = init_ip or '192.168.0.0'
        max_src_ip = ipaddress.ip_address(init_ip) + nb_src - 1
        max_dst_ip = ipaddress.ip_address(init_ip) + nb_dst - 1

        if port is None:
            port = self.port

        if not self.ready:
            self.wait_ready(stdout=False, stderr=False)

        commands = [
            f'range {port} dst port start {init_port}',
            f'range {port} src ip max {max_src_ip}',
            f'range {port} dst ip max {max_dst_ip}',
            f'range {port} size start {pkt_size}',
            f'range {port} size min {pkt_size}',
            f'range {port} size max {pkt_size}',
        ]
        self.commands(commands)

    def start(self, throughput: float, nb_pkts: int,
              port: Optional[int] = None) -> None:
        port = port or self.port

        if not self.ready:
            self.wait_ready(stdout=False, stderr=False)

        commands = []
        if self.use_pcap:
            commands += [f'enable {port} pcap']

        rate = (throughput / self.max_throughput * 100)

        self.target_pkt_tx = self.get_nb_tx_pkts() + nb_pkts

        commands += [
            f'set {port} count {nb_pkts}',
            f'set {port} rate {rate}',
            f'start {port}',
        ]
        self.commands(commands)

    def wait_transmission_done(self) -> None:
        pkts_tx = 0
        while pkts_tx < self.target_pkt_tx:
            time.sleep(1)

            old_pkt_tx = pkts_tx
            pkts_tx = self.get_nb_tx_pkts()

            if old_pkt_tx == pkts_tx:
                raise RuntimeError('Pktgen is not making progress')

    def stop(self, port: Optional[int] = None) -> None:
        self.commands(f'stop {port or self.port}')

    def clear(self) -> None:
        self.pktgen.send('clr\n')
        self.wait_ready()

    def clean_stats(self) -> None:
        return self.clear()

    def close(self) -> None:
        self.pktgen.send('quit\n')
        time.sleep(0.1)
        self.pktgen_ssh_client.close()

    def _get_stats(self, subtype: str, stat_name: str, port: int) -> int:
        self.pktgen.send(
            f'\nlua \'print(pktgen.portStats("all", "{subtype}")[{port}].'
            f'{stat_name})\'\n'
        )
        output = watch_command(
            self.pktgen, keyboard_int=lambda: self.pktgen.send('\x03'),
            stop_pattern='\r\n\\d+\r\n'
        )
        lines = output.split('\r\n')
        lines = [ln for ln in lines if ln.isdigit()]

        return int(lines[-1])

    def get_pkts_rx_rate(self, port: Optional[int] = None) -> int:
        return self._get_stats('rate', 'pkts_rx', port or self.port)

    def get_pkts_tx_rate(self, port: Optional[int] = None) -> int:
        return self._get_stats('rate', 'pkts_tx', port or self.port)

    def get_mbits_rx(self, port: Optional[int] = None) -> int:
        return self._get_stats('rate', 'mbits_rx', port or self.port)

    def get_mbits_tx(self, port: Optional[int] = None) -> int:
        return self._get_stats('rate', 'mbits_rx', port or self.port)

    def get_nb_rx_pkts(self, port: Optional[int] = None) -> int:
        return self._get_stats('port', 'ipackets', port or self.port)

    def get_nb_tx_pkts(self, port: Optional[int] = None) -> int:
        return self._get_stats('port', 'opackets', port or self.port)

    def get_rx_throughput(self, port: Optional[int] = None) -> int:
        return self.get_mbits_rx(self.port) * 1_000_000

    def get_tx_throughput(self, port: Optional[int] = None) -> int:
        return self.get_mbits_tx(self.port) * 1_000_000

    def enter_interactive(self) -> None:
        posix_shell(self.pktgen)

    def __del__(self) -> None:
        self.commands('quit')
        del self.pktgen
        self.pktgen_ssh_client.close()
        del self.pktgen_ssh_client
