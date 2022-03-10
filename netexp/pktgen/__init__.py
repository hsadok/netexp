
class Pktgen:
    def start(self, rate, nb_pkts):
        raise NotImplementedError

    def wait_transmission_done(self):
        raise NotImplementedError

    def clean_stats(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError
