import unittest
from unittest.mock import patch
from scripts import sample_faculty_verification as sample


class FieldSampleTests(unittest.TestCase):
    @patch.object(sample, 'fetch_radar_topic', return_value={'id': 7, 'requested_query': 'AI security'})
    @patch.object(sample, 'get_db_connection')
    def test_field_filters_candidates_and_exact_supporting_papers(self, connection, _topic):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [[{'id': 21, 'sample_field': 'AI security'}], [{'id': 1, 'matched_query': 'AI security'}]]
        result = sample.candidates(10, 20260831, [5, 6], field='AI security')
        candidate_call, paper_call = cursor.execute.call_args_list
        self.assertEqual(candidate_call.args[1], ['AI security', [5, 6], 7, '20260831', 10])
        self.assertIn('e.is_current_match AND rtp.is_current_match', candidate_call.args[0])
        self.assertEqual(paper_call.args[1], (21, 7))
        self.assertIn('e.radar_topic_id = %s AND e.is_current_match', paper_call.args[0])
        self.assertEqual(result[0]['recent_papers'][0]['matched_query'], 'AI security')

    @patch.object(sample, 'fetch_radar_topic', return_value=None)
    @patch.object(sample, 'get_db_connection')
    def test_unknown_field_fails_before_external_discovery(self, connection, _topic):
        with self.assertRaises(ValueError):
            sample.candidates(10, field='Unknown field')
        connection.assert_not_called()

    @patch.object(sample, 'get_db_connection')
    def test_original_random_and_replay_parameters_unchanged(self, connection):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        sample.candidates(10, 42, [9])
        self.assertEqual(cursor.execute.call_args.args[1], [[9], '42', 10])
        sample.candidates(10, replay_ids=[3, 4])
        self.assertEqual(cursor.execute.call_args.args[1], [[3, 4], [3, 4], 10])

    def test_replay_does_not_silently_ignore_field(self):
        with self.assertRaises(ValueError):
            sample.candidates(10, replay_ids=[1], field='AI security')

    @patch.object(sample, 'get_db_connection')
    def test_recent_opt_in_removes_only_schedule_not_manual_or_conflict_safeguards(self, connection):
        cursor = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        sample.candidates(10, 42, include_recent=True)
        query = cursor.execute.call_args.args[0]
        self.assertNotIn('next_identity_check_at', query)
        self.assertIn("faculty_status IS DISTINCT FROM 'CONFLICT'", query)
        self.assertIn("faculty_verification_method IS DISTINCT FROM 'manual_review'", query)
