
from netexp.helpers import remote_command, watch_command


# TODO(sadok): Provide this as a method for pktgen. This can allow it to
# flexibly get the packet size if a pcap is loaded.
def mean_pkt_size_remote_pcap(ssh_client, pcap_path) -> float:
    capinfos_cmd = remote_command(
        ssh_client, f'capinfos -z {pcap_path}', pty=True
    )
    output = watch_command(capinfos_cmd,
                           keyboard_int=lambda: capinfos_cmd.send('\x03'))
    status = capinfos_cmd.recv_exit_status()
    if status != 0:
        raise RuntimeError('Error processing remote pcap')

    try:
        parsed_output = output.split(' ')[-2]
        mean_pcap_pkt_size = float(parsed_output)
    except (IndexError, ValueError):
        raise RuntimeError(
            f'Error processing remote pcap (capinfos output: "{output}"')

    return mean_pcap_pkt_size
