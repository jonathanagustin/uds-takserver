#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests of image/licence_gate.py and of the tracked licence-policy.json.

Run from the repository root:
  python3 -B -m unittest takx/desktop-prototype/image/test_licence_gate.py
No Docker, no network and no assembled tree are needed: every tree is built
in a temporary directory.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import licence_gate  # noqa: E402

POLICY = os.path.join(HERE, 'licence-policy.json')
ELF = b'\x7fELF\x02\x01\x01' + b'\0' * 57


def small_policy():
    return {
        'allow': {'Apache-2.0': 'ok', 'GPL-3.0-only': 'ok', 'MIT': 'ok'},
        'review': {'EPL-2.0': 'needs an opinion'},
        'deny': {'MPL-1.1': 'denied', 'CDDL-1.0': 'denied', 'LicenseRef-FFmpeg-nonfree': 'denied',
                 'LicenseRef-unlicensed-font': 'denied'},
        'deny_content': [{'id': 'nonfree', 'bytes': 'nonfree and unredistributable',
                          'licence': 'LicenseRef-FFmpeg-nonfree', 'reason': 'FFmpeg'}],
        'deny_names': [{'id': 'font', 'licence': 'LicenseRef-unlicensed-font', 'reason': 'font',
                        'pattern': r'(?i)(^|/)(arial|cour|segoe)[a-z0-9]*_m?sdf\.(png|json)$'}],
        'acknowledgements': [{'licence': 'EPL-2.0', 'text': 'Includes EPL software.'}],
        'maven': {'g:a:1.0': {'licence': 'Apache-2.0', 'evidence': 'POM'},
                  'g:epl:1.0': {'licence': 'EPL-2.0'},
                  'g:dual:1.0': {'licence': 'MPL-1.1 OR Apache-2.0'},
                  'g:mpl:1.0': {'licence': 'MPL-1.1'},
                  'g:both:1.0': {'licence': 'Apache-2.0 AND MPL-1.1'},
                  'g:odd:1.0': {'licence': 'Some-Unlisted-Licence'},
                  'g:natives:1.0:linux': {'licence': 'Apache-2.0'}},
        'files': [{'glob': 'conf/*', 'origin_prefix': 'official', 'licence': 'GPL-3.0-only'},
                  {'glob': 'data/*', 'licence': 'MIT', 'review': 'provenance open', 'aggregate': True},
                  {'glob': 'load/*.jar', 'origin_prefix': 'client stage', 'licence': 'GPL-3.0-only'},
                  {'glob': '*wizard*.jar', 'licence': 'CDDL-1.0'}],
        'natives': [{'glob': 'native/libgood.so', 'licence': 'GPL-3.0-only'},
                    {'glob': 'lib/natives-1.0-linux.jar!/natives/*', 'licence': 'Apache-2.0'}],
        'embedded': [{'glob': 'lib/a-1.0.jar!/fonts/ok.ttf', 'licence': 'Apache-2.0'}],
    }


class TreeCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tree = os.path.join(self.temporary.name, 'tree')
        os.makedirs(self.tree)
        self.files = []
        policy = small_policy()
        self.policy_path = os.path.join(self.temporary.name, 'policy.json')
        with open(self.policy_path, 'w', encoding='utf-8') as stream:
            json.dump(policy, stream)

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, relative, data, origin, listed=True):
        full = os.path.join(self.tree, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as stream:
            stream.write(data)
        if listed:
            self.files.append({'path': relative, 'origin': origin})

    def jar(self, relative, entries, origin, listed=True):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        self.put(relative, buffer.getvalue(), origin, listed)

    def run_gate(self, notices=None):
        with open(os.path.join(self.tree, 'manifest.json'), 'w', encoding='utf-8') as stream:
            json.dump({'files': self.files}, stream)
        gate = licence_gate.Gate(licence_gate.load_policy(self.policy_path))
        gate.tree(self.tree)
        if notices:
            gate.notices(notices)
        return gate.report()

    def flagged(self, report):
        return {(item['path'], item['verdict']) for item in report['flagged']}

    def clean_tree(self):
        self.jar('lib/a-1.0.jar', {'x/A.class': b'\xca\xfe\xba\xbe'}, 'public Maven artifact g:a:1.0 (https://repo)')
        self.put('conf/a.xml', b'<a/>', 'official TAKX 5.7 deployment/bin')
        self.put('native/libgood.so', ELF, 'built here')

    def test_clean_tree_is_clean(self):
        self.clean_tree()
        report = self.run_gate()
        self.assertTrue(report['clean'])
        self.assertTrue(report['build_may_proceed'])
        self.assertIsNone(report['publishable'])
        self.assertEqual(report['counts'], {'allow': 3, 'review': 0, 'unknown': 0, 'deny': 0})
        self.assertEqual(report['licences_in_use'], ['Apache-2.0', 'GPL-3.0-only'])

    def test_unknown_maven_artifact_fails(self):
        self.clean_tree()
        self.jar('lib/new-2.0.jar', {'x/B.class': b'x'}, 'public Maven artifact g:a:2.0 (https://repo)')
        report = self.run_gate()
        self.assertFalse(report['build_may_proceed'])
        self.assertIn(('lib/new-2.0.jar', 'unknown'), self.flagged(report))

    def test_denied_and_unlisted_identifiers(self):
        self.jar('lib/mpl.jar', {'x': b'x'}, 'public Maven artifact g:mpl:1.0 (r)')
        self.jar('lib/both.jar', {'x': b'x'}, 'public Maven artifact g:both:1.0 (r)')
        self.jar('lib/odd.jar', {'x': b'x'}, 'public Maven artifact g:odd:1.0 (r)')
        self.jar('lib/dual.jar', {'x': b'x'}, 'public Maven artifact g:dual:1.0 (r)')
        report = self.run_gate()
        self.assertEqual(self.flagged(report), {('lib/mpl.jar', 'deny'), ('lib/both.jar', 'deny'), ('lib/odd.jar', 'unknown')})
        self.assertFalse(report['build_may_proceed'])

    def test_review_keeps_the_build_but_not_clean(self):
        self.clean_tree()
        self.jar('lib/epl.jar', {'x': b'x'}, 'public Maven artifact g:epl:1.0 (r)')
        report = self.run_gate()
        self.assertTrue(report['build_may_proceed'])
        self.assertFalse(report['clean'])
        self.assertIs(report['publishable'], False)
        self.assertEqual(self.flagged(report), {('lib/epl.jar', 'review')})

    def test_content_string_denies_whatever_the_pom_says(self):
        self.jar('lib/natives-1.0-linux.jar',
                 {'natives/libavutil.so.56': ELF + b'libavutil license: nonfree and unredistributable\0'},
                 'public Maven artifact g:natives:1.0:linux (r)')
        self.put('conf/readme.txt', b'this build is nonfree and unredistributable', 'official TAKX 5.7')
        report = self.run_gate()
        self.assertIn(('lib/natives-1.0-linux.jar!/natives/libavutil.so.56', 'deny'), self.flagged(report))
        self.assertIn(('conf/readme.txt', 'deny'), self.flagged(report))
        self.assertFalse(report['build_may_proceed'])

    def test_font_atlas_names_are_denied_inside_jars_and_loose(self):
        self.jar('load/kernel.jar', {'assets/fonts/arial_msdf.png': b'png', 'assets/fonts/segoeuib_msdf.json': b'{}',
                                     'assets/fonts/font_resources.json': b'{}'}, 'client stage')
        self.put('conf/courbd_msdf.png', b'png', 'official TAKX 5.7')
        report = self.run_gate()
        self.assertEqual(self.flagged(report), {('load/kernel.jar!/assets/fonts/arial_msdf.png', 'deny'),
                                                ('load/kernel.jar!/assets/fonts/segoeuib_msdf.json', 'deny'),
                                                ('conf/courbd_msdf.png', 'deny')})

    def test_native_and_font_inside_a_jar_need_their_own_rule(self):
        self.jar('lib/a-1.0.jar', {'natives/libx.so': ELF, 'fonts/ok.ttf': b'f', 'fonts/other.ttf': b'f'},
                 'public Maven artifact g:a:1.0 (r)')
        self.jar('lib/natives-1.0-linux.jar', {'natives/liby.so': ELF}, 'public Maven artifact g:natives:1.0:linux (r)')
        report = self.run_gate()
        self.assertEqual(self.flagged(report), {('lib/a-1.0.jar!/natives/libx.so', 'unknown'),
                                                ('lib/a-1.0.jar!/fonts/other.ttf', 'unknown')})

    def test_loose_native_and_asset_without_rule_and_unlisted_file(self):
        self.put('native/libother.so', ELF, 'built here')
        self.put('misc/file.bin', b'data', 'somewhere')
        self.put('conf/extra.xml', b'<a/>', 'official TAKX 5.7', listed=False)
        self.put('conf/wrong-origin.xml', b'<a/>', 'copied from elsewhere')
        self.files.append({'path': 'conf/gone.xml', 'origin': 'official TAKX 5.7'})
        report = self.run_gate()
        self.assertEqual(self.flagged(report), {('native/libother.so', 'unknown'), ('misc/file.bin', 'unknown'),
                                                ('conf/extra.xml', 'unknown'), ('conf/wrong-origin.xml', 'unknown'),
                                                ('conf/gone.xml', 'unknown')})

    def test_denied_jar_by_file_rule_and_unreadable_jar(self):
        self.jar('lib/ui-wizard-reference.jar', {'x': b'x'}, 'public source rebuild')
        self.put('load/broken.jar', b'not a zip', 'client stage')
        report = self.run_gate()
        self.assertIn(('lib/ui-wizard-reference.jar', 'deny'), self.flagged(report))
        self.assertIn(('load/broken.jar', 'unknown'), self.flagged(report))

    def zip_bytes(self, entries):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def test_archives_inside_jars_and_loose_archives_are_opened(self):
        self.clean_tree()
        # Compressed, so the raw bytes of the outer files do not hold the string.
        inner = self.zip_bytes({'lib/libavutil.so.56': ELF + b'libavutil license: nonfree and unredistributable' * 20})
        self.jar('lib/a-1.0.jar', {'x/A.class': b'\xca\xfe\xba\xbe', 'bundled/natives.zip': inner},
                 'public Maven artifact g:a:1.0 (https://repo)')
        self.put('conf/icons.zip', self.zip_bytes({'set/arial_msdf.png': b'png' * 50, 'set/tool': ELF,
                                                    'set/ok.png': b'png'}), 'official TAKX 5.7 deployment/bin')
        report = self.run_gate()
        flagged = self.flagged(report)
        self.assertIn(('lib/a-1.0.jar!/bundled/natives.zip!/lib/libavutil.so.56', 'deny'), flagged)
        self.assertIn(('conf/icons.zip!/set/arial_msdf.png', 'deny'), flagged)
        self.assertIn(('conf/icons.zip!/set/tool', 'unknown'), flagged)
        self.assertFalse(report['build_may_proceed'])
        # The archives themselves are still judged by their own rules.
        self.assertNotIn(('conf/icons.zip', 'unknown'), flagged)

    def test_unreadable_and_too_deeply_nested_archives_are_unknown(self):
        self.clean_tree()
        self.put('conf/broken.zip', licence_gate.ZIP_MAGIC + b'not an archive at all', 'official TAKX 5.7 deployment/bin')
        data = self.zip_bytes({'leaf.txt': b'text'})
        for level in range(licence_gate.MAX_DEPTH + 1):
            data = self.zip_bytes({'level%d.zip' % level: data})
        self.put('conf/deep.zip', data, 'official TAKX 5.7 deployment/bin')
        report = self.run_gate()
        reasons = {item['path']: item['reason'] for item in report['flagged']}
        self.assertIn('cannot be read', reasons['conf/broken.zip'])
        deep = [path for path, reason in reasons.items() if 'nested deeper' in reason]
        self.assertEqual(len(deep), 1)
        self.assertEqual(deep[0].count('!/'), licence_gate.MAX_DEPTH + 1)
        self.assertFalse(report['build_may_proceed'])

    def test_a_plain_nested_archive_changes_nothing(self):
        self.clean_tree()
        self.put('conf/icons.zip', self.zip_bytes({'set/a.png': b'png', 'set/more.zip': self.zip_bytes({'b.png': b'png'})}),
                 'official TAKX 5.7 deployment/bin')
        report = self.run_gate()
        self.assertTrue(report['clean'])
        self.assertEqual(report['counts']['allow'], 4)

    def test_aggregated_review_is_one_item_with_a_count(self):
        for index in range(5):
            self.put('data/%d.csv' % index, b'1', 'data directory')
        report = self.run_gate()
        self.assertEqual(len(report['flagged']), 1)
        self.assertEqual((report['flagged'][0]['path'], report['flagged'][0]['files']), ('data/*', 5))
        self.assertTrue(report['build_may_proceed'])

    def test_notices_need_texts_and_acknowledgements(self):
        self.clean_tree()
        self.jar('lib/epl.jar', {'x': b'x'}, 'public Maven artifact g:epl:1.0 (r)')
        notices = os.path.join(self.temporary.name, 'notices')
        os.makedirs(os.path.join(notices, 'LICENSES', 'texts'))
        report = self.run_gate(notices)
        missing = {item['path'] for item in report['flagged'] if item['kind'] == 'notice'}
        self.assertEqual(missing, {'LICENSES/texts/Apache-2.0.txt', 'LICENSES/texts/EPL-2.0.txt',
                                   'LICENSES/texts/GPL-3.0-only.txt', 'NOTICE.md'})
        self.assertFalse(report['build_may_proceed'])
        for name in ('Apache-2.0', 'EPL-2.0', 'GPL-3.0-only'):
            with open(os.path.join(notices, 'LICENSES', 'texts', name + '.txt'), 'w', encoding='utf-8') as stream:
                stream.write('licence text ' * 20)
        with open(os.path.join(notices, 'NOTICE.md'), 'w', encoding='utf-8') as stream:
            stream.write('# Notice\n\nIncludes EPL\nsoftware.\n')
        report = self.run_gate(notices)
        self.assertEqual(self.flagged(report), {('lib/epl.jar', 'review')})
        self.assertTrue(report['build_may_proceed'])

    def test_command_line_exit_codes_and_report(self):
        self.clean_tree()
        with open(os.path.join(self.tree, 'manifest.json'), 'w', encoding='utf-8') as stream:
            json.dump({'files': self.files}, stream)
        report = os.path.join(self.temporary.name, 'report.json')
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(licence_gate.main(['--tree', self.tree, '--policy', self.policy_path, '--report', report]), 0)
            with open(report, encoding='utf-8') as stream:
                self.assertTrue(json.load(stream)['clean'])
            self.put('misc/file.bin', b'data', 'somewhere')
            with open(os.path.join(self.tree, 'manifest.json'), 'w', encoding='utf-8') as stream:
                json.dump({'files': self.files}, stream)
            self.assertEqual(licence_gate.main(['--tree', self.tree, '--policy', self.policy_path]), 1)
            self.assertEqual(licence_gate.main(['--tree', self.tree, '--policy', os.path.join(self.tree, 'none.json')]), 2)


class ExpressionCase(unittest.TestCase):
    def test_expressions(self):
        policy = small_policy()
        self.assertEqual(licence_gate.evaluate(policy, 'Apache-2.0'), ('allow', ['Apache-2.0']))
        self.assertEqual(licence_gate.evaluate(policy, 'MPL-1.1 OR Apache-2.0'), ('allow', ['Apache-2.0']))
        self.assertEqual(licence_gate.evaluate(policy, 'MPL-1.1 OR EPL-2.0'), ('review', ['EPL-2.0']))
        self.assertEqual(licence_gate.evaluate(policy, 'Apache-2.0 AND EPL-2.0')[0], 'review')
        self.assertEqual(licence_gate.evaluate(policy, 'Apache-2.0 AND MPL-1.1')[0], 'deny')
        self.assertEqual(licence_gate.evaluate(policy, 'Apache-2.0 AND Nothing')[0], 'unknown')
        self.assertEqual(licence_gate.evaluate(policy, '')[0], 'unknown')
        self.assertEqual(licence_gate.evaluate(policy, None)[0], 'unknown')

    def test_identifier_in_two_lists_is_refused(self):
        policy = small_policy()
        policy['review']['MIT'] = 'twice'
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, 'policy.json')
            with open(path, 'w', encoding='utf-8') as stream:
                json.dump(policy, stream)
            with self.assertRaises(licence_gate.PolicyError):
                licence_gate.load_policy(path)


class TrackedPolicyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = licence_gate.load_policy(POLICY)

    def test_header_and_seeded_deny_list(self):
        self.assertIn('SPDX-License-Identifier: GPL-3.0-only', self.policy['$comment'])
        for identifier in ('LicenseRef-FFmpeg-nonfree', 'CDDL-1.0', 'MPL-1.1', 'LicenseRef-unlicensed-font'):
            self.assertIn(identifier, self.policy['deny'])
        self.assertIn('nonfree and unredistributable', [rule['bytes'] for rule in self.policy['deny_content']])

    def test_every_expression_uses_listed_identifiers(self):
        rules = list(self.policy['maven'].values())
        for key in ('files', 'natives', 'embedded'):
            rules += self.policy[key]
        self.assertGreater(len(rules), 150)
        for rule in rules:
            for alternative in rule['licence'].split(' OR '):
                for identifier in alternative.split(' AND '):
                    self.assertNotEqual(licence_gate.verdict_of_identifier(self.policy, identifier), 'unknown',
                                        '%r in %r' % (identifier, rule['licence']))
            self.assertTrue(rule.get('evidence'), rule)

    def test_every_usable_identifier_has_a_text_source(self):
        texts = self.policy['texts']
        sources = set(texts['spdx']) | set(texts['compose']) | set(texts['embedded']) | set(texts['statement'])
        for identifier in list(self.policy['allow']) + list(self.policy['review']):
            self.assertIn(identifier, sources)
        for digest in texts['spdx'].values():
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
        for parts in texts['compose'].values():
            for part in parts:
                self.assertIn(part, texts['spdx'])

    def test_the_removed_artifacts_stay_denied(self):
        self.assertEqual(licence_gate.evaluate(self.policy, self.policy['maven']['net.sf.joost:joost:0.9.1']['licence'])[0], 'deny')
        self.assertEqual(licence_gate.evaluate(
            self.policy, self.policy['maven']['org.bytedeco:ffmpeg:4.1.3-1.5.1:linux-x86_64']['licence'])[0], 'deny')
        rule = licence_gate.match_rule(self.policy['files'], 'modules/prototype/libs/main/uds-takserver-ui-wizard-reference.jar',
                                       'public source rebuild by takx/ui-java wizard reference')
        self.assertEqual(licence_gate.evaluate(self.policy, rule['licence'])[0], 'deny')
        for name in ('assets/fonts/arial_msdf.png', 'assets/fonts/courbi_msdf.json', 'assets/fonts/segoeuiz_msdf.png',
                     'fonts/Arial.ttf', 'x/segoeui.ttf'):
            self.assertTrue(licence_gate.name_hits(self.policy, name), name)
        for name in ('assets/fonts/font_resources.json', 'FONTS/UnitFont.ttf', 'resources/timestamp.png',
                     'icons/courier_truck.png', 'fonts/DejaVuSans.ttf'):
            self.assertFalse(licence_gate.name_hits(self.policy, name), name)

    def test_name_patterns_compile_and_are_anchored(self):
        for rule in self.policy['deny_names']:
            self.assertIsInstance(rule['regex'], re.Pattern)
            self.assertTrue(rule['pattern'].endswith('$'))


if __name__ == '__main__':
    unittest.main()
