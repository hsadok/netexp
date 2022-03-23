
from netexp.pktgen import Pktgen


def zero_loss_throughput(pktgen: Pktgen, mean_pkt_size: int,
                         max_throughput: int = 100_000_000_000,
                         precision: int = 1_000_000_000,
                         target_duration: int = 5) -> int:
    """Find zero-loss throughput using a binary search.

    This assumes that the DUT is connected to the packet generator and ready to
    receive packets. The DUT should try to send back all packets that it
    receives and should not introduce new ones. The DUT is allowed to drop
    packets if it cannot keep up with the input throughput.

    Args:
        dut: Design Under Test.
        pktgen: Packet generator.
        mean_pkt_size: Mean packet size (in bytes) that will be sent.
        max_throughput: Maximum throughout to try (in bps).
        precision: Throughput precision (in bps).
        target_duration: Target experiment duration (in seconds). The number of
          packets to be sent will be adjusted base on this and the current
          attempted throughput.

    Returns:
        The zero loss throughput found (in bps).
    """
    def get_nb_pkts_for_throughput(throughput):
        pps = throughput / ((mean_pkt_size + 20) * 8)
        return int(pps * target_duration)

    tpt_lower = 0  # Lower bound.
    tpt_upper = max_throughput  # Upper bound.

    # We start from the maximum.
    current_throughput = max_throughput

    # We iteratively refine the bounds until the difference between them is
    # less than the specified precision.
    while (tpt_upper - tpt_lower) > precision:
        nb_pkts = get_nb_pkts_for_throughput(current_throughput)

        print(f'Trying {current_throughput // 1e6} Mbps with {nb_pkts} pkts.')

        pktgen.clean_stats()
        pktgen.start(current_throughput, nb_pkts)
        pktgen.wait_transmission_done()

        nb_rx_pkts = pktgen.get_nb_rx_pkts()

        if nb_rx_pkts < nb_pkts:
            tpt_upper = current_throughput
        elif nb_rx_pkts == nb_pkts:
            tpt_lower = current_throughput
        else:  # nb_rx_pkts > nb_pkts
            raise RuntimeError(
                'Received more packets than sent. Measurement is unreliable.'
            )

        current_throughput = (tpt_upper + tpt_lower) // 2

    # Found a rate.
    return tpt_lower
