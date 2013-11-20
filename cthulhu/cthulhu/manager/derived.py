from collections import defaultdict
from cthulhu.manager.types import OsdMap, OsdTree, PgBrief, MdsMap, MonStatus

PG_FIELDS = ['pgid', 'acting', 'up', 'state']
OSD_FIELDS = ['uuid', 'up', 'in', 'up_from', 'public_addr',
              'cluster_addr', 'heartbeat_back_addr', 'heartbeat_front_addr']


CRIT_STATES = set(['stale', 'down', 'peering', 'inconsistent', 'incomplete'])
WARN_STATES = set(['creating', 'recovery_wait', 'recovering', 'replay',
                   'splitting', 'degraded', 'remapped', 'scrubbing', 'repair',
                   'wait_backfill', 'backfilling', 'backfill_toofull'])
OKAY_STATES = set(['active', 'clean'])


class OsdPgDetail(object):
    depends = [OsdMap, PgBrief, OsdTree]

    @classmethod
    def generate(cls, data):
        osd_map = data[OsdMap]
        osd_tree = data[OsdTree]
        pgs_brief = data[PgBrief]
        # map osd id to pg states
        pg_states_by_osd = defaultdict(lambda: defaultdict(lambda: 0))
        # map osd id to set of pools
        pools_by_osd = defaultdict(lambda: set([]))
        # map pg state to osd ids
        osds_by_pg_state = defaultdict(lambda: set([]))

        # helper to modify each pg object
        def fixup_pg(pg):
            data = dict((k, pg[k]) for k in PG_FIELDS)
            data['state'] = data['state'].split("+")
            return data

        # save the brief pg map
        pgs = map(fixup_pg, pgs_brief)

        # get the list of pools
        pools_by_id = dict((d['pool'], d['pool_name']) for d in osd_map['pools'])

        # populate the indexes
        for pg in pgs:
            pool_id = int(pg['pgid'].split(".")[0])
            acting = set(pg['acting'])
            for state in pg['state']:
                osds_by_pg_state[state] |= acting
                for osd_id in acting:
                    pg_states_by_osd[osd_id][state] += 1
                    if pool_id in pools_by_id:
                        pools_by_osd[osd_id] |= set([pools_by_id[pool_id]])

        # convert set() to list to make JSON happy
        osds_by_pg_state = dict((k, list(v)) for k, v in
                                osds_by_pg_state.iteritems())
        osds_by_pg_state = osds_by_pg_state

        # get the osd tree. we'll use it to get hostnames
        nodes_by_id = dict((n["id"], n) for n in osd_tree["nodes"])

        # FIXME: this assumes that an osd node is a direct descendent of a
        #
        # host. It also assumes that these node types are called 'osd', and
        # 'host' respectively. This is probably not as general as we would like
        # it. Some clusters might have weird crush maps. This also assumes that
        # the host name in the crush map is the same host name reported by
        # Diamond. It is fragile.
        host_by_osd_name = defaultdict(lambda: None)
        for node in osd_tree["nodes"]:
            if node["type"] == "host":
                host = node["name"]
                for id in node["children"]:
                    child = nodes_by_id[id]
                    if child["type"] == "osd":
                        host_by_osd_name[child["name"]] = host

        # helper to modify each osd object
        def fixup_osd(osd):
            osd_id = osd['osd']
            data = dict((k, osd[k]) for k in OSD_FIELDS)
            data.update({'id': osd_id})
            data.update({'osd': osd_id})
            data.update({'pg_states': pg_states_by_osd[osd_id]})
            data.update({'pools': list(pools_by_osd[osd_id])})
            data.update({'host': host_by_osd_name["osd.%d" % (osd_id,)]})
            return data

        # add the pg states to each osd
        osds = map(fixup_osd, osd_map['osds'])

        return {
            'osds': osds,
            'osds_by_pg_state': osds_by_pg_state,
            'pgs': pgs
        }


class HealthCounters(object):
    depends = [OsdMap, MdsMap, MonStatus, PgBrief]

    @classmethod
    def generate(cls, data):
        return {'counters': {
            'osd': cls._calculate_osd_counters(data[OsdMap]),
            'mds': cls._calculate_mds_counters(data[MdsMap]),
            'mon': cls._calculate_mon_counters(data[MonStatus]),
            'pg': cls._calculate_pg_counters(data[PgBrief]),
        }}

    @classmethod
    def _calculate_mon_counters(cls, mon_status):
        mons = mon_status['monmap']['mons']
        quorum = mon_status['quorum']
        ok, warn, crit = 0, 0, 0
        for mon in mons:
            rank = mon['rank']
            if rank in quorum:
                ok += 1
            # TODO: use 'have we had a salt heartbeat recently' here instead
            #elif self.try_mon_connect(mon):
            #    warn += 1
            else:
                crit += 1
        return {
            'ok': {
                'count': ok,
                'states': {} if ok == 0 else {'in': ok},
            },
            'warn': {
                'count': warn,
                'states': {} if warn == 0 else {'up': warn},
            },
            'critical': {
                'count': crit,
                'states': {} if crit == 0 else {'out': crit},
            }
        }

    @classmethod
    def _pg_counter_helper(cls, states, classifier, count, stats):
        matched_states = classifier.intersection(states)
        if len(matched_states) > 0:
            stats[0] += count
            for state in matched_states:
                stats[1][state] += count
            return True
        return False

    @classmethod
    def _calculate_pg_counters(cls, pg_map):
        # Although the mon already has a copy of this (in 'status' output),
        # it's such a simple thing to recalculate here and simplifies our
        # sync protocol.
        pgs_by_state = defaultdict(int)
        for pg in pg_map:
            pgs_by_state[pg['state']] += 1

        ok, warn, crit = [[0, defaultdict(int)] for _ in range(3)]
        for state_name, count in pgs_by_state.items():
            states = map(lambda s: s.lower(), state_name.split("+"))
            if cls._pg_counter_helper(states, CRIT_STATES, count, crit):
                pass
            elif cls._pg_counter_helper(states, WARN_STATES, count, warn):
                pass
            elif cls._pg_counter_helper(states, OKAY_STATES, count, ok):
                pass
        return {
            'ok': {
                'count': ok[0],
                'states': ok[1],
            },
            'warn': {
                'count': warn[0],
                'states': warn[1],
            },
            'critical': {
                'count': crit[0],
                'states': crit[1],
            },
        }
    #
    #
    #def _calculate_pool_counters():
    #    fields = ['num_objects_unfound', 'num_objects_missing_on_primary',
    #              'num_deep_scrub_errors', 'num_shallow_scrub_errors',
    #              'num_scrub_errors', 'num_objects_degraded']
    #    counts = defaultdict(lambda: 0)
    #    pools = self.client.get_pg_pools()
    #    for pool in imap(lambda p: p['stat_sum'], pools):
    #        for key, value in pool.items():
    #            counts[key] += min(value, 1)
    #    for delkey in set(counts.keys()) - set(fields):
    #        del counts[delkey]
    #    counts['total'] = len(pools)
    #    return counts

    @classmethod
    def _calculate_osd_counters(cls, osd_map):
        osds = osd_map['osds']
        counters = {
            'total': len(osds),
            'not_up_not_in': 0,
            'not_up_in': 0,
            'up_not_in': 0,
            'up_in': 0
        }
        for osd in osds:
            up, inn = osd['up'], osd['in']
            if not up and not inn:
                counters['not_up_not_in'] += 1
            elif not up and inn:
                counters['not_up_in'] += 1
            elif up and not inn:
                counters['up_not_in'] += 1
            elif up and inn:
                counters['up_in'] += 1
        warn_count = counters['up_not_in'] + counters['not_up_in']
        warn_states = {}
        if counters['up_not_in'] > 0:
            warn_states['up/out'] = counters['up_not_in']
        if counters['not_up_in'] > 0:
            warn_states['down/in'] = counters['not_up_in']
        return {
            'ok': {
                'count': counters['up_in'],
                'states': {} if counters['up_in'] == 0 else {'up/in': counters['up_in']},
            },
            'warn': {
                'count': warn_count,
                'states': {} if warn_count == 0 else warn_states,
            },
            'critical': {
                'count': counters['not_up_not_in'],
                'states': {} if counters['not_up_not_in'] == 0 else {'down/out': counters['not_up_not_in']},
            },
        }

    @classmethod
    def _calculate_mds_counters(cls, mds_map):
        total = mds_map['max_mds']
        up = len(mds_map['up'])
        inn = len(mds_map['in'])
        return {
            'total': total,
            'up_in': inn,
            'up_not_in': up-inn,
            'not_up_not_in': total-up,
        }


generators = [OsdPgDetail, HealthCounters]