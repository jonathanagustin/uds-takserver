#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Licence gate of the TAKX desktop image: every JAR, native library and asset
of the assembled tree must be identified and carry an allowed licence.

  licence_gate.py --tree TREE [--policy licence-policy.json] [--notices DIR]
                  [--report REPORT.json]

Exit status 1 fails the image build: an item is DENIED or UNKNOWN, or (with
--notices) the text of a licence in use or a required acknowledgement is
missing. Exit status 0 with items under REVIEW is a build that may exist but
must not be published; the report says "clean": false and lists them.

The gate reads the tree itself, not a list somebody wrote: it walks every
file, opens every JAR and looks at every entry. It is not a legal review; it
enforces a recorded policy (licence-policy.json next to this script):

  allow / review / deny   licence identifiers. 'review' is a licence that is
                          known and permits redistribution but whose
                          combination with GPL-3.0 code, or whose notice
                          duties, somebody qualified has to confirm.
  deny_content            byte strings that deny any file or JAR entry that
                          contains them, whatever its metadata says (the
                          FFmpeg build that calls itself "nonfree and
                          unredistributable" sat in a JAR whose POM says
                          Apache-2.0).
  deny_names              name patterns of files and JAR entries that are
                          denied (font files and font atlases named after
                          proprietary fonts).
  maven                   'group:artifact:version[:classifier]' -> licence
                          expression as declared by the POM. Another version
                          is UNKNOWN until somebody looks at it.
  files                   path patterns, with the origin recorded by
                          manifest.json, -> licence expression, for everything
                          that is not a Maven artifact.
  natives                 native libraries: loose files and 'JAR!/entry'
                          patterns. A native library inside a JAR is UNKNOWN
                          unless a rule here names it.
  embedded                fonts and audio inside JARs.

An expression is identifiers joined by ' AND ' (all apply) or ' OR ' (a
choice; the best alternative counts). A rule may carry "review": "<reason>",
which keeps an otherwise allowed item out of a clean result; with
"aggregate": true the files such a rule matches are reported as one item
(a data directory of a hundred files with one open question).
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import zipfile

ORDER = {'allow': 0, 'review': 1, 'unknown': 2, 'deny': 3}
FAILING = ('unknown', 'deny')
MAVEN_ORIGIN = re.compile(r'^public Maven artifact (\S+) \(')
ELF_MAGIC = b'\x7fELF'
FONT_OR_AUDIO = re.compile(r'\.(ttf|otf|ttc|woff2?|pfb|pfa|fnt|bdf|wav|mp3|ogg|au|aiff?)$|_m?sdf\.(png|json)$', re.I)


class PolicyError(Exception):
    pass


def load_policy(path):
    with open(path, encoding='utf-8') as stream:
        policy = json.load(stream)
    for key in ('allow', 'review', 'deny', 'deny_content', 'deny_names', 'maven', 'files', 'natives', 'embedded'):
        if key not in policy:
            raise PolicyError('policy has no "%s"' % key)
    listed = [set(policy[key]) for key in ('allow', 'review', 'deny')]
    if listed[0] & listed[1] or listed[0] & listed[2] or listed[1] & listed[2]:
        raise PolicyError('a licence identifier is in more than one of allow, review, deny')
    for rule in policy['deny_names']:
        rule['regex'] = re.compile(rule['pattern'])
    return policy


def verdict_of_identifier(policy, identifier):
    for verdict in ('deny', 'review', 'allow'):
        if identifier in policy[verdict]:
            return verdict
    return 'unknown'


def evaluate(policy, expression):
    """Return (verdict, identifiers of the alternative that counts)."""
    if not expression or not isinstance(expression, str):
        return 'unknown', []
    best = None
    for alternative in expression.split(' OR '):
        identifiers = [part.strip() for part in alternative.split(' AND ')]
        if not all(identifiers):
            return 'unknown', []
        worst = max((verdict_of_identifier(policy, identifier) for identifier in identifiers), key=ORDER.get)
        if best is None or ORDER[worst] < ORDER[best[0]]:
            best = (worst, identifiers)
    return best


def worse(first, second):
    return first if ORDER[first] >= ORDER[second] else second


def match_rule(rules, path, origin=''):
    for rule in rules:
        if fnmatch.fnmatchcase(path, rule['glob']) and origin.startswith(rule.get('origin_prefix', '')):
            return rule
    return None


def content_hits(policy, data):
    return [rule for rule in policy['deny_content'] if rule['bytes'].encode('utf-8') in data]


def name_hits(policy, name):
    return [rule for rule in policy['deny_names'] if rule['regex'].search(name)]


class Gate:
    def __init__(self, policy):
        self.policy = policy
        self.items = []
        self.used = set()
        self.aggregated = {}

    def add(self, path, kind, verdict, licence, reason, rule=None):
        self.items.append({'path': path, 'kind': kind, 'verdict': verdict, 'licence': licence, 'reason': reason,
                           'evidence': (rule or {}).get('evidence', '')})

    def judge(self, path, kind, rule, missing):
        """Record an item from its rule; return the verdict."""
        if rule is None:
            self.add(path, kind, 'unknown', None, missing)
            return 'unknown'
        verdict, identifiers = evaluate(self.policy, rule.get('licence'))
        reason = ''
        if verdict == 'unknown':
            reason = 'licence expression %r has an identifier that is in none of allow, review, deny' % rule.get('licence')
        elif verdict == 'deny':
            reason = '; '.join('%s: %s' % (i, self.policy['deny'][i]) for i in identifiers if i in self.policy['deny'])
        elif verdict == 'review':
            reason = '; '.join('%s: %s' % (i, self.policy['review'][i]) for i in identifiers if i in self.policy['review'])
        if rule.get('review') and verdict == 'allow':
            verdict, reason = 'review', rule['review']
        elif rule.get('review'):
            reason = '%s; %s' % (reason, rule['review'])
        if verdict in ('allow', 'review'):
            self.used.update(identifiers)
        if rule.get('aggregate') and verdict == 'review':
            group = self.aggregated.setdefault(rule['glob'], {'kind': kind, 'licence': rule.get('licence'),
                                                              'reason': reason, 'rule': rule, 'files': 0})
            group['files'] += 1
            return verdict
        self.add(path, kind, verdict, rule.get('licence'), reason, rule)
        return verdict

    def scan(self, path, name, data):
        """Deny rules that look at the bytes and the name; True when one hit."""
        hit = False
        for rule in content_hits(self.policy, data):
            self.add(path, 'content', 'deny', rule['licence'], 'contains %r: %s' % (rule['bytes'], rule['reason']))
            hit = True
        for rule in name_hits(self.policy, name):
            self.add(path, 'name', 'deny', rule['licence'], 'name matches %s: %s' % (rule['id'], rule['reason']))
            hit = True
        return hit

    def jar(self, relative, full, origin):
        maven = MAVEN_ORIGIN.match(origin)
        if maven:
            rule = self.policy['maven'].get(maven.group(1))
            missing = 'Maven artifact %s is not in the policy' % maven.group(1)
        else:
            rule = match_rule(self.policy['files'], relative, origin)
            missing = 'no "files" rule for this JAR with origin %r' % origin
        self.judge(relative, 'jar', rule, missing)
        try:
            archive = zipfile.ZipFile(full)
        except zipfile.BadZipFile:
            self.add(relative, 'jar', 'unknown', None, 'not a readable ZIP archive')
            return
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                inner = '%s!/%s' % (relative, info.filename)
                data = archive.read(info)
                if self.scan(inner, info.filename, data):
                    continue
                if data[:4] == ELF_MAGIC:
                    self.judge(inner, 'native', match_rule(self.policy['natives'], inner),
                               'native library inside a JAR without a "natives" rule')
                elif FONT_OR_AUDIO.search(info.filename):
                    self.judge(inner, 'embedded', match_rule(self.policy['embedded'], inner),
                               'font, font atlas or audio inside a JAR without an "embedded" rule')

    def tree(self, tree):
        with open(os.path.join(tree, 'manifest.json'), encoding='utf-8') as stream:
            origins = {entry['path']: entry['origin'] for entry in json.load(stream)['files']}
        seen = set()
        for folder, dirs, files in os.walk(tree):
            dirs.sort()
            for name in sorted(files):
                full = os.path.join(folder, name)
                relative = os.path.relpath(full, tree).replace(os.sep, '/')
                if relative == 'manifest.json':
                    continue
                seen.add(relative)
                if os.path.islink(full) or not os.path.isfile(full):
                    self.add(relative, 'file', 'unknown', None, 'not a regular file')
                    continue
                if relative not in origins:
                    self.add(relative, 'file', 'unknown', None, 'not in manifest.json: no recorded origin')
                    continue
                origin = origins[relative]
                if name.endswith('.jar'):
                    self.jar(relative, full, origin)
                    continue
                with open(full, 'rb') as stream:
                    data = stream.read()
                if self.scan(relative, relative, data):
                    continue
                if data[:4] == ELF_MAGIC:
                    self.judge(relative, 'native', match_rule(self.policy['natives'], relative),
                               'native library without a "natives" rule')
                else:
                    self.judge(relative, 'asset', match_rule(self.policy['files'], relative, origin),
                               'no "files" rule for this path with origin %r' % origin)
        for relative in sorted(set(origins) - seen):
            self.add(relative, 'file', 'unknown', None, 'in manifest.json but not in the tree')
        for glob, group in sorted(self.aggregated.items()):
            self.add(glob, group['kind'], 'review', group['licence'], group['reason'], group['rule'])
            self.items[-1]['files'] = group['files']
        self.aggregated = {}

    def notices(self, directory):
        """Texts of the licences in use and the acknowledgements some of them demand."""
        for identifier in sorted(self.used):
            text = os.path.join(directory, 'LICENSES', 'texts', identifier.replace(' ', '_') + '.txt')
            if not os.path.isfile(text) or os.path.getsize(text) < 100:
                self.add('LICENSES/texts/%s.txt' % identifier.replace(' ', '_'), 'notice', 'unknown', identifier,
                         'the text of a licence in use is missing from the notices')
        notice = ''
        path = os.path.join(directory, 'NOTICE.md')
        if os.path.isfile(path):
            with open(path, encoding='utf-8') as stream:
                notice = ' '.join(stream.read().split())
        for wanted in self.policy.get('acknowledgements', []):
            if wanted['licence'] in self.used and ' '.join(wanted['text'].split()) not in notice:
                self.add('NOTICE.md', 'notice', 'unknown', wanted['licence'],
                         'NOTICE.md lacks the acknowledgement this licence demands: %r' % wanted['text'])

    def report(self):
        counts = {verdict: 0 for verdict in ORDER}
        kinds = {}
        for item in self.items:
            counts[item['verdict']] += 1
            kinds.setdefault(item['kind'], {verdict: 0 for verdict in ORDER})[item['verdict']] += 1
        flagged = [item for item in self.items if item['verdict'] != 'allow']
        return {'schema': 1,
                'clean': not flagged,
                'build_may_proceed': not any(item['verdict'] in FAILING for item in self.items),
                'publishable': False if flagged else None,
                'publishable_note': 'false while anything is flagged; a clean gate is necessary, not sufficient',
                'counts': counts, 'counts_by_kind': kinds,
                'licences_in_use': sorted(self.used),
                'flagged': sorted(flagged, key=lambda item: (-ORDER[item['verdict']], item['path'])),
                'items': len(self.items)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--tree', required=True)
    parser.add_argument('--policy', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'licence-policy.json'))
    parser.add_argument('--notices')
    parser.add_argument('--report')
    options = parser.parse_args(argv)
    try:
        gate = Gate(load_policy(options.policy))
    except (PolicyError, OSError, ValueError) as error:
        print('licence_gate: %s' % error, file=sys.stderr)
        return 2
    gate.tree(options.tree)
    if options.notices:
        gate.notices(options.notices)
    report = gate.report()
    if options.report:
        temporary = options.report + '.tmp.%d' % os.getpid()
        with open(temporary, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, indent=1)
            stream.write('\n')
        os.replace(temporary, options.report)
    print(json.dumps({key: report[key] for key in ('clean', 'build_may_proceed', 'counts', 'items')}))
    for item in report['flagged']:
        print('%-7s %s [%s] %s' % (item['verdict'].upper(), item['path'], item['licence'], item['reason']), file=sys.stderr)
    return 0 if report['build_may_proceed'] else 1


if __name__ == '__main__':
    sys.exit(main())
