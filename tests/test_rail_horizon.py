import unittest
from national_fixture import DAY,train,cancellation,loc
from national.rail import RailMessages
from national.common import ImportErrorDetail


class RailHorizonTests(unittest.TestCase):
    def test_bounded_export_matches_an_unbounded_export_for_retained_days(self):
        full=RailMessages();bounded=RailMessages(DAY,'2026-10-28')
        for message in [train(),cancellation(bits='010')]:
            full.add(message);bounded.add(message)
        left,_=full.export(DAY,'2026-10-28');right,_=bounded.export(DAY,'2026-10-28')
        for key in ['stops','routes','trips','active','paths','transfers']:
            self.assertEqual(left[key],right[key])

    def test_newest_inactive_revision_removes_an_older_active_path(self):
        store=RailMessages(DAY,DAY)
        store.add(train(bits='100',stamp='2026-10-01T12:00:00Z'))
        store.add(train(bits='010',stamp='2026-10-02T12:00:00Z'))
        graph,_=store.export(DAY,DAY)
        self.assertEqual(graph['trips'],[])
        self.assertEqual(next(iter(store.paths.values()))['points'],[])

    def test_conflicting_inactive_revisions_are_still_detected(self):
        store=RailMessages(DAY,DAY);store.add(train(bits='010'))
        changed=train(bits='010',points=[loc('A','Different A','06:00:00'),loc('C','C','09:00:00')])
        with self.assertRaisesRegex(ImportErrorDetail,'Conflicting'):store.add(changed)

    def test_irrelevant_old_cancellation_does_not_require_guessing_its_clock(self):
        message=cancellation(bits='100',stamp='2026-10-25T02:30:00')
        outside=RailMessages('2026-10-28','2026-10-28')
        outside.add(message)
        self.assertEqual(outside.inactive_cancellations,1)
        graph,_=outside.export('2026-10-28','2026-10-28');self.assertEqual(graph['trips'],[])
        with self.assertRaisesRegex(ImportErrorDetail,'timezone'):
            RailMessages(DAY,DAY).add(message)

    def test_export_cannot_claim_dates_discarded_during_import(self):
        store=RailMessages(DAY,DAY);store.add(train())
        with self.assertRaisesRegex(ImportErrorDetail,'outside'):store.export(DAY,'2026-10-27')


if __name__=='__main__':
    unittest.main()
