"""Private-context and source-identity boundaries using synthetic fixtures."""
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from zotero.figure_identity import validate_figures
from zotero.source_links import public_url
from zotero.note_structure import public_region


class FigureReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'assets').mkdir()
        self.asset = self.root/'assets/asset-001.png'
        self.asset.write_bytes(b'synthetic-image-content')
        self.pdf = self.root/'paper.pdf'; self.pdf.write_bytes(b'synthetic-pdf-content')
        self.record = {'filename': self.asset.name, 'sha256': hashlib.sha256(self.asset.read_bytes()).hexdigest(), 'figures': ['3'], 'status': 'verified', 'evidence': {'page': 6, 'method': 'rendered-pdf-visual-comparison'}}
        self.ledger = {'schema': 2, 'source_pdf_sha256': hashlib.sha256(self.pdf.read_bytes()).hexdigest(), 'assets': [self.record]}
        self.note = '---\nnote_contract: 2\n---\n![Figure 3. Left panel](assets/asset-001.png)'
        self.write_ledger()

    def write_ledger(self):
        (self.root/'assets/figure-ledger.json').write_text(json.dumps(self.ledger), encoding='utf-8')

    def validate(self):
        return validate_figures(self.note, self.root/'note.md', self.pdf)

    def test_same_figure_panels_are_valid(self):
        self.note += '\n![Figure 3. Right panel](assets/asset-001.png)'
        self.assertEqual(self.validate(), [])

    def test_changed_pdf_invalidates_review(self):
        self.pdf.write_bytes(b'new-version')
        self.assertIn('figure-review-required', self.validate())

    def test_changed_image_invalidates_review(self):
        self.asset.write_bytes(b'new-crop')
        self.assertIn('figure-review-required', self.validate())

    def test_wrong_alt_identity_is_rejected(self):
        self.note = self.note.replace('Figure 3', 'Figure 6')
        self.assertIn('figure-review-required', self.validate())

    def test_caption_candidates_do_not_count_as_verified(self):
        self.record['status'] = 'needs-review'; self.write_ledger()
        self.assertIn('figure-review-required', self.validate())

    def test_appendix_id_is_preserved(self):
        self.record['figures'] = ['B.3']; self.write_ledger()
        self.note = self.note.replace('Figure 3', 'Figure B.3')
        self.assertEqual(self.validate(), [])

    def test_table_identity_is_recorded_and_published(self):
        self.record['figures'] = ['Table 1']; self.write_ledger()
        self.note = self.note.replace('Figure 3', 'Table 1')
        self.assertEqual(self.validate(), [])

    def test_algorithm_and_roman_table_ids(self):
        self.record['figures'] = ['Algorithm 1']; self.write_ledger()
        self.note = self.note.replace('Figure 3', 'Algorithm 1')
        self.assertEqual(self.validate(), [])
        self.record['figures'] = ['Table IV']; self.write_ledger()
        self.note = self.note.replace('Algorithm 1', 'Table IV')
        self.assertEqual(self.validate(), [])

    def test_bare_number_in_ledger_is_a_figure(self):
        self.assertEqual(self.validate(), [])

    def test_kind_mismatch_is_rejected(self):
        self.record['figures'] = ['Table 3']; self.write_ledger()
        self.assertIn('figure-review-required', self.validate())

    def test_formatted_private_headings_have_the_same_boundary(self):
        for heading in ['## 我的判断：', '## **我的判断**', '## 11. 项目关联与本笔记判读']:
            self.assertEqual(public_region('## Method\nEvidence\n'+heading+'\nHidden'), '## Method\nEvidence\n')

    def test_fenced_private_heading_does_not_truncate_source(self):
        self.assertIn('Evidence', public_region('```md\n## 我的判断\n```\n## Results\nEvidence'))
        self.assertNotIn('Hidden', public_region('## Results\nEvidence\n## 我的判断\nHidden\n## Later\nHidden'))


class PublicSourceUrlTest(unittest.TestCase):
    @patch('zotero.source_links.section', return_value={'private_hosts': ['private.example.org']})
    def test_public_url_rules(self, _section):
        for url in ['https://user:pass@example.org', 'http://192.168.1.1', 'https://[::1]', 'https://private.example.org', 'https://example.org/?access_token=secret', 'file:///tmp/test', 'https://localhost', 'https://internal']:
            with self.subTest(url=url): self.assertIsNone(public_url(url))
        self.assertEqual(public_url('https://doi.org/10.0000/example'), 'https://doi.org/10.0000/example')


if __name__ == '__main__': unittest.main()
