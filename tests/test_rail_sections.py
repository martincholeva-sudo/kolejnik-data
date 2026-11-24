import unittest
import xml.etree.ElementTree as ET
from national_fixture import DAY,train,loc,cancellation
from national.rail import RailMessages,parse
from national.common import ImportErrorDetail,descendants


def flag(point,name,value):
    node=ET.fromstring(point)
    parameter=ET.SubElement(node,'NetworkSpecificParameter')
    ET.SubElement(parameter,'Name').text=name
    ET.SubElement(parameter,'Value').text=value
    return ET.tostring(node,encoding='unicode')


class RailSectionTests(unittest.TestCase):
    def test_replacement_is_explicit_and_does_not_guess_a_bus(self):
        points=[flag(loc('A','A','06:00:00'),'CZAlternativeTransport','1'),
                loc('B','B','07:00:00')]
        store=RailMessages();store.add(train(points=points))
        network,report=store.export(DAY,DAY)
        route=network['routes'][network['trips'][0][2]]
        self.assertTrue(route[1].startswith('NAD '))
        self.assertTrue(route[3]['replacementTransport'])
        self.assertEqual(route[3]['replacementVehicleType'],'unknown')
        self.assertEqual(report['replacementJourneyVariants'],1)

    def test_mode_change_splits_legs_but_keeps_original_boarding_and_times(self):
        points=[loc('A','A','06:00:00'),
                flag(loc('B','B','07:00:00',activity='0029'),'CZAlternativeTransport','1'),
                flag(loc('C','C','07:30:00'),'CZAlternativeTransport','1'),
                flag(loc('D','D','08:00:00'),'CZAlternativeTransport','0'),
                loc('E','E','09:00:00')]
        store=RailMessages();store.add(train(points=points))
        network,_=store.export(DAY,DAY)
        self.assertEqual(len(network['trips']),3)
        segments=sorted(network['trips'],key=lambda t:t[5][0][2])
        self.assertEqual([network['routes'][t[2]][3]['replacementTransport'] for t in segments],[False,True,False])
        self.assertNotEqual(segments[0][5][-1][0],segments[1][5][0][0])
        a=network['stops'][segments[0][5][-1][0]];b=network['stops'][segments[1][5][0][0]]
        self.assertEqual(a['group'],b['group']);self.assertEqual(b['sourceStopId'],a['id'])
        self.assertEqual(b['boardingMode'],'replacement');self.assertFalse(b['physicalPlatformVerified'])
        self.assertEqual(segments[1][5][0][3],1)
        self.assertEqual(segments[1][5][0][2],7*3600)
        self.assertEqual(network['paths'],[])
        self.assertEqual(network['transfers'],[])

    def test_distinct_replacement_services_do_not_share_an_unverified_road_boarding_node(self):
        points=[flag(loc('A','A','06:00:00'),'CZAlternativeTransport','1'),loc('B','B','07:00:00')]
        store=RailMessages();store.add(train(path='NAD-ONE',points=points));store.add(train(path='NAD-TWO',points=points))
        network,report=store.export(DAY,DAY)
        self.assertEqual(len(network['trips']),2)
        nodes=[network['stops'][trip[5][0][0]] for trip in network['trips']]
        self.assertNotEqual(nodes[0]['id'],nodes[1]['id'])
        self.assertEqual(nodes[0]['group'],nodes[1]['group']);self.assertEqual(nodes[0]['sourceStopId'],nodes[1]['sourceStopId'])
        self.assertEqual(network['paths'],[]);self.assertEqual(network['transfers'],[])

    def test_operator_change_keeps_one_through_vehicle(self):
        points=[loc('A','A','06:00:00',operator='ONE'),loc('B','B','07:00:00',operator='TWO'),loc('C','C','08:00:00',operator='TWO')]
        store=RailMessages();store.add(train(points=points))
        network,_=store.export(DAY,DAY)
        self.assertEqual(len(network['trips']),1)
        self.assertEqual([p['id'] for p in network['routes'][0][3]['operators']],['ONE','TWO'])

    def test_unknown_or_repeated_flags_cannot_be_silently_treated_as_normal_rail(self):
        for point in [flag(loc('A','A','06:00:00'),'CZAlternativeTransport','2'),
                      flag(flag(loc('A','A','06:00:00'),'CZAlternativeTransport','1'),'CZAlternativeTransport','1')]:
            with self.assertRaisesRegex(ImportErrorDetail,'point flag'):
                parse(train(points=[point,loc('B','B','07:00:00')]))

    def test_unpublished_backwards_timing_is_not_a_passenger_stop_or_a_missing_train(self):
        points=[loc('A','A','06:00:00'),loc('X','TECHNICAL ONLY','00:00:00',activity='0004'),loc('B','B','07:00:00')]
        root=train(points=points);store=RailMessages();store.add(root)
        self.assertEqual(parse(root)['points'][1]['arrival'],0)
        network,report=store.export(DAY,DAY)
        self.assertEqual([stop['name'] for stop in network['stops']],['A','B'])
        self.assertEqual(network['trips'][0][5][0][2],6*3600)
        self.assertEqual(network['trips'][0][5][-1][1],7*3600)
        self.assertTrue(report['completeTimingsExported'])
        self.assertEqual(report['nonPassengerTimingPointVisitsExcluded'],1)

    def test_partial_cancellation_still_uses_an_unpublished_source_endpoint(self):
        points=[loc('A','A','06:00:00'),loc('X','TECHNICAL ONLY','06:30:00',activity='0004'),
                loc('B','B','07:00:00'),loc('C','C','08:00:00')]
        store=RailMessages();store.add(train(points=points));store.add(cancellation(section=('A','X')))
        network,_=store.export('2026-10-27','2026-10-27')
        trip=network['trips'][0]
        self.assertEqual([network['stops'][r[0]]['name'] for r in trip[5]],['B','C'])
        self.assertEqual(network['trips'][0][5][0][2],7*3600)

    def test_carrier_change_at_an_unpublished_point_is_retained(self):
        points=[loc('A','A','06:00:00',operator='ONE'),
                loc('X','TECHNICAL ONLY','06:30:00',operator='TWO',activity='0004'),
                loc('B','B','07:00:00',operator='TWO')]
        store=RailMessages();store.add(train(points=points))
        network,_=store.export(DAY,DAY)
        self.assertEqual([p['id'] for p in network['routes'][0][3]['operators']],['ONE','TWO'])
        self.assertEqual(len(network['trips'][0][5]),2)

    def test_declared_countertime_preserves_both_public_event_times(self):
        root=train()
        node=descendants(root,'CZPTTLocation')[1]
        descendants(node,'Time')[1].text='06:58:00'
        parameter=ET.SubElement(node,'NetworkSpecificParameter')
        ET.SubElement(parameter,'Name').text='CZInconsistentTime'
        ET.SubElement(parameter,'Value').text='1'
        record=parse(root)
        self.assertEqual(record['points'][1]['arrival'],7*3600)
        self.assertEqual(record['points'][1]['departure'],6*3600+58*60)
        self.assertTrue(record['points'][1]['inconsistentTime'])
        store=RailMessages();store.add(root)
        network,report=store.export(DAY,DAY)
        self.assertEqual(len(network['trips']),1)
        self.assertEqual(network['trips'][0][5][1][1:3],[7*3600,6*3600+58*60])
        self.assertTrue(report['completeTimingsExported'])
        self.assertEqual(report['declaredCounterTimeJourneyVariants'],1)
        self.assertEqual(report['unrepresentableJourneys'],[])

    def test_unmarked_countertime_still_blocks_export(self):
        root=train()
        node=descendants(root,'CZPTTLocation')[1]
        descendants(node,'Time')[1].text='06:58:00'
        store=RailMessages();store.add(root)
        with self.assertRaisesRegex(ImportErrorDetail,'source-time review'):
            store.export(DAY,DAY)

    def test_declared_countertime_cannot_reverse_the_order_of_departures(self):
        root=train()
        node=descendants(root,'CZPTTLocation')[1]
        descendants(node,'Time')[1].text='05:58:00'
        parameter=ET.SubElement(node,'NetworkSpecificParameter')
        ET.SubElement(parameter,'Name').text='CZInconsistentTime'
        ET.SubElement(parameter,'Value').text='1'
        store=RailMessages();store.add(root)
        with self.assertRaisesRegex(ImportErrorDetail,'source-time review'):
            store.export(DAY,DAY)



if __name__=='__main__':unittest.main()
