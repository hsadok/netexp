from netexp.helpers import LocalHost, RemoteHost

from typing import Union


# TODO(sadok): Provide this as a method for pktgen. This can allow it to
# flexibly get the packet size if a pcap is loaded.
def mean_pkt_size_pcap(
    host: Union[LocalHost, RemoteHost], pcap_path: str
) -> float:
    capinfos_cmd = host.run_command(f"capinfos -z {pcap_path}", pty=True)
    output = capinfos_cmd.watch(keyboard_int=lambda: capinfos_cmd.send("\x03"))
    status = capinfos_cmd.recv_exit_status()

    if status != 0:
        raise RuntimeError("Error processing remote pcap")

    try:
        parsed_output = output.split(" ")[-2]
        mean_pcap_pkt_size = float(parsed_output)
    except (IndexError, ValueError):
        raise RuntimeError(
            f'Error processing remote pcap (capinfos output: "{output}"'
        )

    return mean_pcap_pkt_size
