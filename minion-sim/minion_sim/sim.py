from SimpleXMLRPCServer import SimpleXMLRPCServer
import argparse
import logging
import os
import threading
import time
from minion_sim.ceph_cluster import CephCluster
from minion_sim.constants import XMLRPC_PORT
from minion_sim.load_gen import LoadGenerator
from minion_sim.minion_launcher import MinionLauncher


PREFIX = 'figment'
DOMAIN = 'imagination.com'


log = logging.getLogger('minion_sim.sim')
log.setLevel(logging.DEBUG)
log.addHandler(logging.StreamHandler())
log.addHandler(logging.FileHandler('minion_sim.sim.log'))


def get_dns(index):
    hostname = "{0}{1:03d}".format(PREFIX, index)
    fqdn = "{0}.{1}".format(hostname, DOMAIN)

    return hostname, fqdn


class MinionSim(threading.Thread):
    def __init__(self, config_dir, count):
        super(MinionSim, self).__init__()
        self._config_dir = config_dir
        self._count = count
        self._server = None
        self._server_available = threading.Event()
        self.minions = {}

        config_file = os.path.join(self._config_dir, 'cluster.json')
        if not os.path.exists(config_file):
            CephCluster.create(config_file, [get_dns(i)[1] for i in range(0, self._count)])

        self.cluster = CephCluster(config_file)

        # An XMLRPC service for the minions' fake ceph plugins to
        # get their state
        self._server = SimpleXMLRPCServer(("localhost", XMLRPC_PORT), allow_none=True)
        self._server.register_instance(self.cluster)

        for i in range(0, self._count):
            hostname, fqdn = get_dns(i)
            self.minions[fqdn] = MinionLauncher(self._config_dir, hostname, fqdn, self.cluster)

        # Quick smoke test that these methods aren't going
        # to throw exceptions (rather stop now than get exceptions
        # remotely)
        for minion in self.minions.values():
            self.cluster.get_stats(minion.fqdn)

    def get_minion_fqdns(self):
        return [m.fqdn for m in self.minions.values()]

    def start(self):
        super(MinionSim, self).start()
        self._server_available.wait()

    def run(self):
        # A thread to generate some synthetic activity on the synthetic cluster
        load_gen = LoadGenerator(self.cluster)
        load_gen.start()

        self.start_minions()

        self._server_available.set()

        log.debug("Starting XMLRPC server...")
        self._server.serve_forever()
        self._server.server_close()
        log.debug("XMLRPC server terminated, stopping threads")

        log.debug("Stopping load gen")
        load_gen.stop()
        load_gen.join()

        log.debug("Stopping minions")
        self.halt_minions()

        log.debug("Saving state")
        self.cluster.save()
        log.debug("Complete.")

    def stop(self):
        self._server.shutdown()

    def halt_minions(self):
        for minion in self.minions.values():
            minion.stop()
        for minion in self.minions.values():
            minion.join()

    def start_minions(self):
        for minion in self.minions.values():
            minion.start()

    def halt_minion(self, minion_id):
        self.minions[minion_id].stop()
        self.minions[minion_id].join()

    def start_minion(self, minion_id):
        self.minions[minion_id].start()


def main():
    parser = argparse.ArgumentParser(description='Start simulated salt minions.')
    parser.add_argument('--count', dest='count', type=int, default=3, help='Number of simulated minions')
    args = parser.parse_args()

    config_path = os.getcwd()

    sim = MinionSim(config_path, args.count)

    log.debug("Starting simulator...")
    sim.start()
    try:
        log.debug("Waiting for simulator...")
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        log.debug("Terminating simulator...")
        sim.stop()
        sim.join()
        log.debug("Complete.")
