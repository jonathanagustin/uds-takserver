#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fresh, opt-in TAKX 5.7 compile for the desktop prototype.

Runs inside the takx-desktop-proto:toolchain image (see ../stages/client.sh).
It compiles the official TAKX 5.7 sources at 0b59a9bb from scratch and never
feeds anything to takx/client-compiler. That recipe's two locks are read only
as the specification of: the source lists, the classpath ORDER, the processor
path, the javac flags and the expected class-name sets. This recipe's own
record of what was actually consumed is inputs.lock.json next to this file.

Steps (each writes P/takx57/<step>/ and P/takx57/receipts/<step>.json):

  prep       Bootstrap preferences, Bootstrap and ProcessManager javac
  xjc        XJC over the seven Platform XSDs plus GlobalBindings.xjb
  templates  java-template expansion as Platform/build.gradle defines it
  platform   whole-Platform javac, real AutoValue + EventBus processors
  aspects    ajc over the four .aj aspects, no weaving, aop/aop.xml
  client     Client javac of all 1,678 sources, real EventBus index
  package    bootstrap, preferences-factory, platform, client, processmanager
             JARs with each module's src/main/resources
  lock       collect every input digest into P/takx57/inputs.lock.json

The Kernel on the classpath is public Kernel 8.30.5 plus the project patches
and CommonCommo is 1.17.2 patched. Nothing here turns them into the private
Kernel 8.57.11 or CommonCommo 1.20.0 that TAKX 5.7 declares.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REVISION = '0b59a9bb2d0e90dc35c9edfff1c2056daab2b713'
ROOT = Path(os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
REPO = Path(os.environ.get('TAKX_PROTO_REPO', Path(__file__).resolve().parents[3]))
JDK = Path('/tools/temurin-17.0.21_9/jdk-17.0.21+9')
SPEC = REPO / 'takx/client-compiler'
SRC = ROOT / 'src/takx57'
OUT = ROOT / 'P/takx57'
CLOSURE = ROOT / 'jars/client-compiler/takx-client-compiler-closure/maven-public'
READINESS = 'takx-client-readiness:5.7/'
PROBE = 'client57-public-interop-compiler-02:probe-5.7/'
KERNEL_REF = 'sdk-package-bounded-stock-qualification:package/kernel-8.30.5-linux-amd64-public-cpu.jar'
INTEROP_REF = 'kernel-client-interop-public-bitmap-controls-01:classes'
TEXTURE_REF = 'takx-client-compiler-closure:texture-compile-only-overlay-families/classes'
CONTEXT_REF = 'render-context-java-client-frozen-01:classes'
OWN_DIRS = {PROBE + 'preferences-classes': 'prep/preferences-classes',
            PROBE + 'bootstrap-classes': 'prep/bootstrap-classes',
            PROBE + 'processmanager-classes': 'prep/processmanager-classes',
            PROBE + 'platform-classes': 'platform/classes',
            PROBE + 'aspect-classes': 'aspects/classes'}
# The javac flags of takx/client-compiler/build.py:212-220 and
# platform_producer.py:261-265, minus the paths.
JAVAC_FLAGS = ['-J-Xmx768m', '-J-XX:ActiveProcessorCount=2', '--release', '17', '-encoding', 'UTF-8',
               '-implicit:none', '-Xmaxerrs', '2000']
ACTUAL = {'kernel': '8.30.5 plus project patches (takx/kernel-texture, takx/kernel-render-context)',
          'commoncommo': '1.17.2 patched', 'requested_private_kernel': '8.57.11',
          'requested_private_commoncommo': '1.20.0',
          'worldwind': 'NASA WorldWind Java v2.2.1 interop profile, not the private 2.2.1-TAKX-4'}


class Failure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    with open(path) as stream:
        return json.load(stream)


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.%d' % os.getpid())
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    os.replace(temporary, path)


def inventory(directory, suffix=''):
    directory = Path(directory)
    return {p.relative_to(directory).as_posix(): sha(p)
            for p in sorted(directory.rglob('*')) if p.is_file() and p.name.endswith(suffix)}


def client_lock():
    return load(SPEC / 'inputs.lock.json')


def platform_lock():
    return load(SPEC / 'platform-inputs.lock.json')


class Step:
    """One output directory, one receipt, one list of recorded inputs."""

    def __init__(self, name):
        self.name = name
        self.dir = OUT / name
        self.inputs = {}
        self.started = time.time()
        self.receipt = {'step': name, 'official_revision': REVISION, 'actual': ACTUAL,
                        'recipe_sha256': sha(__file__)}
        if self.dir.exists():
            (ROOT / 'failed').mkdir(exist_ok=True)
            n = 1
            while (ROOT / 'failed' / ('takx57-%s-%d' % (name, n))).exists():
                n += 1
            shutil.move(str(self.dir), str(ROOT / 'failed' / ('takx57-%s-%d' % (name, n))))
        (OUT / 'receipts').mkdir(parents=True, exist_ok=True)
        stale = OUT / 'receipts' / (name + '.json')
        if stale.exists():
            stale.unlink()
        self.dir.mkdir(parents=True)

    def use(self, kind, path, expected=None, note=None):
        """Record one consumed input file; fail closed on a pinned digest."""
        path = Path(path)
        require(path.is_file(), 'input is missing: %s' % path)
        digest = sha(path)
        if expected is not None:
            require(digest == expected, 'input digest differs from its pin: %s' % path)
        entry = {'kind': kind, 'sha256': digest}
        if note:
            entry['note'] = note
        self.inputs[str(path)] = entry
        return path

    def source(self, relative):
        """An official 5.7 source file, pinned by the client-compiler lock."""
        files = client_lock()['files']
        key = READINESS + relative
        require(key in files, 'official source is not pinned by the specification: ' + relative)
        return self.use('official-source', SRC / relative, files[key])

    def run(self, label, command, cwd=None, timeout=1500):
        log = self.dir / (label + '.log')
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('JAVA_', 'JDK_', 'LD_')) and k not in ('_JAVA_OPTIONS', 'CLASSPATH', 'DISPLAY')}
        env.update(LC_ALL='C.UTF-8', TZ='UTC')
        started = time.time()
        with open(log, 'wb') as stream:
            code = subprocess.run(command, cwd=cwd or self.dir, env=env, stdin=subprocess.DEVNULL,
                                  stdout=stream, stderr=subprocess.STDOUT, timeout=timeout).returncode
        text = log.read_text(errors='replace')
        result = {'command': [str(c) for c in command], 'exit': code, 'seconds': round(time.time() - started, 1),
                  'log': str(log), 'log_sha256': sha(log),
                  'errors': len(re.findall(r'(?m)^.*\.(?:java|aj):\d+(?::\d+::\d+)?:? ?(?:error:|\[error\])', text))
                  + len(re.findall(r'(?m)^(?:error:|\[error\])', text)),
                  'warnings': len(re.findall(r'(?m)warning:', text))}
        self.receipt.setdefault('commands', {})[label] = result
        require(code == 0 and result['errors'] == 0,
                '%s failed: exit %s, %s compiler errors; see %s' % (label, code, result['errors'], log))
        return result

    def finish(self, verified=True, problem=None, **extra):
        """Write the receipt. The step is ok only if its verification passed."""
        self.receipt.update(extra)
        self.receipt['inputs'] = self.inputs
        self.receipt['seconds'] = round(time.time() - self.started, 1)
        self.receipt['status'] = 'ok' if verified else 'failed'
        if not verified:
            self.receipt['failure'] = problem
        dump(OUT / 'receipts' / (self.name + '.json'), self.receipt)
        require(verified, problem)
        print('%s ok in %ss' % (self.name, self.receipt['seconds']))


def jdk(step):
    """The pinned JDK identity: the four files the specification binds."""
    files = client_lock()['files']
    for name in ['bin/java', 'bin/javac', 'release', 'lib/modules']:
        step.use('jdk', JDK / name, files['tools:temurin-17.0.21_9/jdk-17.0.21+9/' + name])
    return JDK / 'bin/javac'


def closure(step, name):
    """A public JAR of the compile closure, pinned by the specification lock."""
    files = client_lock()['files']
    return step.use('public-jar', CLOSURE / name, files['takx-client-compiler-closure:maven-public/' + name])


def write_args(path, sources):
    require(not any(c in str(p) for p in sources for c in '\n\r\0'), 'unsupported source path')
    path.write_text(''.join('"' + str(p).replace('\\', '\\\\').replace('"', '\\"') + '"\n' for p in sources))
    return '@' + str(path)


def compare(actual, expected):
    """Class-name set comparison (the acceptance) and hash equality (informational)."""
    names, wanted = set(actual), set(expected)
    return {'classes': len(names), 'expected_names': len(wanted), 'names_equal': names == wanted,
            'missing_names': sorted(wanted - names), 'unexpected_names': sorted(names - wanted),
            'hash_equal_informational': sum(1 for n in names & wanted
                                            if isinstance(expected, dict) and actual[n] == expected[n])
            if isinstance(expected, dict) else None}


def step_done(name):
    receipt = OUT / 'receipts' / (name + '.json')
    return receipt.exists() and load(receipt).get('status') == 'ok'


# --------------------------------------------------------------------------
def prep():
    step = Step('prep')
    javac = jdk(step)
    lock = client_lock()
    empty = step.dir / 'empty'
    empty.mkdir()
    listing = {key[len(READINESS):] for key in lock['files'] if key.startswith(READINESS)}

    def module(name, prefix, classpath, expected_ref):
        sources = sorted(k for k in listing if k.startswith(prefix) and k.endswith('.java'))
        on_disk = sorted(p.relative_to(SRC).as_posix() for p in (SRC / prefix).rglob('*.java'))
        require(sources == on_disk, '%s source membership differs from the specification' % name)
        out = step.dir / (name + '-classes')
        out.mkdir()
        command = [javac, *JAVAC_FLAGS, '-sourcepath', empty, '-d', out]
        if classpath:
            command += ['-cp', os.pathsep.join(map(str, classpath))]
        command.append(write_args(step.dir / (name + '.sources.args'), [step.source(s) for s in sources]))
        step.run(name, command)
        result = compare(inventory(out, '.class'), lock['class_directories'][expected_ref])
        result['sources'] = len(sources)
        require(result['names_equal'], '%s class names differ from the expected set' % name)
        return result

    # Classpaths follow Bootstrap/build.gradle and ProcessManager/build.gradle.
    slf4j = closure(step, 'org.slf4j--slf4j-api-2.0.17.jar')
    bootstrap_cp = [step.dir / 'preferences-classes'] + [closure(step, n) for n in [
        'org.jboss.modules--jboss-modules-1.12.0.Final.jar', 'org.aspectj--aspectjrt-1.9.21.1.jar',
        'org.aspectj--aspectjweaver-1.9.21.1.jar', 'org.slf4j--slf4j-api-2.0.17.jar',
        'org.apache.logging.log4j--log4j-api-2.25.4.jar', 'org.apache.logging.log4j--log4j-core-2.25.4.jar',
        'org.apache.logging.log4j--log4j-slf4j2-impl-2.25.4.jar']]
    results = {
        'preferences': module('preferences', 'Bootstrap/src/preferences/java/', [], PROBE + 'preferences-classes'),
        'bootstrap': module('bootstrap', 'Bootstrap/src/main/java/', bootstrap_cp, PROBE + 'bootstrap-classes'),
        'processmanager': module('processmanager', 'ProcessManager/src/main/java/', [slf4j],
                                 PROBE + 'processmanager-classes')}
    step.finish(modules=results)


# --------------------------------------------------------------------------
XJC_JARS = ['org.glassfish.jaxb--jaxb-xjc-3.0.1.jar', 'org.glassfish.jaxb--jaxb-core-3.0.1.jar',
            'org.glassfish.jaxb--jaxb-runtime-3.0.1.jar', 'org.glassfish.jaxb--codemodel-3.0.1.jar',
            'org.glassfish.jaxb--xsom-3.0.1.jar', 'org.glassfish.jaxb--txw2-3.0.1.jar',
            'com.sun.xml.bind.external--rngom-3.0.1.jar', 'com.sun.xml.bind.external--relaxng-datatype-3.0.1.jar',
            'com.sun.xml.dtd-parser--dtd-parser-1.4.3.jar', 'com.sun.istack--istack-commons-tools-4.0.1.jar',
            'com.sun.istack--istack-commons-runtime-4.0.1.jar', 'jakarta.xml.bind--jakarta.xml.bind-api-3.0.1.jar',
            'com.sun.activation--jakarta.activation-2.0.1.jar']


def xjc():
    step = Step('xjc')
    jdk(step)
    schemas = SRC / 'Platform/src/main/resources/schemas'
    xsds = sorted(schemas.glob('*.xsd'))
    bindings = sorted(schemas.glob('*.xjb'))
    require(len(xsds) == 7 and [b.name for b in bindings] == ['GlobalBindings.xjb'], 'unexpected schema set')
    for path in xsds + bindings:
        step.use('official-schema', path)
    classpath = [closure(step, name) for name in XJC_JARS]
    out = step.dir / 'xjc'
    out.mkdir()
    # What buildSupport.configureXjc plus com.github.bjornvester.xjc 1.9.0 do
    # (XjcWorker.kt): extension mode because binding files exist, XML security
    # features off, -quiet, every *.xsd and every *.xjb of the schema
    # directory. -no-header is this recipe's one addition: the default file
    # header carries the generation time, which no receipt could reproduce.
    command = [JDK / 'bin/java', '-Xmx512m', '-XX:ActiveProcessorCount=1', '-Dfile.encoding=UTF-8',
               '-cp', os.pathsep.join(map(str, classpath)), 'com.sun.tools.xjc.Driver',
               '-extension', '-disableXmlSecurity', '-quiet', '-no-header', '-encoding', 'UTF-8', '-d', out]
    for binding in bindings:
        command += ['-b', binding]
    command += xsds
    step.run('xjc', command)
    produced = inventory(out, '.java')
    frozen = {k[len(PROBE + 'generated/xjc/'):]: v for k, v in platform_lock()['sources'].items()
              if k.startswith(PROBE + 'generated/xjc/')}
    result = compare(produced, frozen)
    result['note'] = 'compared with the 70 frozen XJC source names of the specification; hashes informational'
    require(result['names_equal'], 'XJC source names differ from the expected 70')
    step.finish(xjc=result, generated=produced)


# --------------------------------------------------------------------------
def gradle_versions(step):
    text = step.source('gradle/versions.gradle').read_text()
    return dict(re.findall(r"(?m)^\s*(\w+)\s*=\s*'([^']*)'", text))


def declared_dependencies(step, versions):
    """The directly declared Platform dependencies, by Gradle configuration."""
    text = step.source('gradle/platformDependencies.gradle').read_text()
    text += '\n' + step.source('Platform/build.gradle').read_text().split('apply from:')[0]
    found = {'api': [], 'implementation': [], 'runtimeOnly': []}
    for config, coordinate in re.findall(r'(?m)^\s*(api|implementation|runtimeOnly)\s*\(?\s*"([^"]+)"', text):
        parts = coordinate.split(':')
        version = re.sub(r'\$\{?(\w+)\}?', lambda m: versions[m.group(1)], parts[2]) if len(parts) > 2 else ''
        found[config].append((parts[0], parts[1], version))
    return found


# Where this build does not use the version Gradle declares, the javadoc
# tables name what is actually on the classpath instead.
SUBSTITUTED = {
    'gov.nasa:worldwind': '2.2.1 (NASA public source, uds-takserver interop profile)',
    'com.github.weisj:jsvg': '1.6.1',
    'net.java.dev:colorchooser': '1.5 (author public source, uds-takserver ui-java)',
    'org.dockingframes:docking-frames-common': '1.1.3p1 (public source rebuild, uds-takserver ui-java)',
    'org.dockingframes:docking-frames-core': '1.1.3p1 (public source rebuild, uds-takserver ui-java)',
}
MODULE_MAPPING = {
    'com.formdev.flatlaf-swingx': 'com.formdev.flatlaf-swingx', 'com.koloboke': 'com.koloboke',
    'io.github.openfeign': 'io.github.openfeign', 'org.apache.logging.log4j': 'log4j',
    'org.bytedeco': 'org.bytedeco:javacpp', 'org.dockingframes': 'org.dockingframes',
    'org.hibernate': 'org.hibernate', 'org.jboss.modules': 'org.jboss.modules',
    'org.jboss.weld': 'org.jboss.weld', 'org.jboss.weld.se': 'org.jboss.weld',
    'org.jogamp.jogl': 'org.jogamp.jogl', 'org.jogamp.jocl': 'org.jogamp.jogl',
    'org.jogamp.gluegen': 'org.jogamp.jogl', 'org.ow2.asm': 'org.ow2.asm',
    'org.swinglabs.swingx.swingx-all': 'org.swinglabs.swingx.swingx-all'}


def library_table(dependencies, exclusions):
    """generateLibraryVersionList of Platform/build.gradle."""
    rows = []
    for group, name, version in sorted(set(dependencies), key=lambda d: '%s:%s' % (d[0], d[1])):
        if group.startswith('gov.tak') or group in exclusions:
            continue
        default = group if group == name or group.endswith('.' + name) else '%s.%s' % (group, name)
        version = SUBSTITUTED.get('%s:%s' % (group, name), version)
        rows.append('\n * <tr>\n * <td>{@code %s:%s}</td>\n * <td>{@code %s}</td>\n * <td>{@code %s}</td>\n * </tr>'
                    % (group, name, version, MODULE_MAPPING.get(group, default)))
    return '\n'.join(rows)


def expand(text, properties, project):
    """Gradle 'expand' for the token forms these three templates use."""
    require('\\' not in text and '<%' not in text, 'template uses syntax this expander does not implement')

    def replace(match):
        name = match.group(1) or match.group(2)
        if name.startswith('project.'):
            require(name[8:] in project, 'template names an unknown project property: ' + name)
            return project[name[8:]]
        require(name in properties, 'template names an unknown property: ' + name)
        return properties[name]
    expanded = re.sub(r'\$\{([\w.]+)\}|\$([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)', replace, text)
    require('$' not in expanded.replace(properties.get('constants', ''), ''), 'unexpanded template token')
    return expanded


def templates():
    step = Step('templates')
    versions = gradle_versions(step)
    platform_version = step.use('official-source', SRC / 'platformVersion.txt').read_text().strip()
    major, minor = (int(part) for part in platform_version.split('.')[:2])
    constants = ['\n    /** @since %d.%d */\n    V%d_%d(new PlatformVersion(%d, %d, 0)),' % (m, n, m, n, m, n)
                 for m, top in ((3, 5), (major, minor)) for n in range(top + 1)]
    declared = declared_dependencies(step, versions)
    runtime = [d for d in declared['implementation'] + declared['runtimeOnly'] if d[1]]
    properties = {
        'constants': '\n'.join(constants),
        'runtimeLibraryVersionList': library_table(runtime, ['com.google.auto.value', 'org.aspectj', 'com.zaxxer']),
        'apiLibraryVersionList': library_table(
            declared['api'], ['gov.nasa', 'gov.tak.kernel', 'jakarta.enterprise', 'jcommoncommo', 'org.slf4j'])}
    # The 'project' properties the templates read. Three of them name private
    # components this build does not have; the generated javadoc states what
    # is actually used instead of the version Gradle declares.
    project = dict(versions)
    project.update(worldwindVersion='2.2.1 (NASA public source, uds-takserver interop profile; not %s)'
                   % versions['worldwindVersion'],
                   takKernelVersion='8.30.5 plus uds-takserver project patches (public source; not %s)'
                   % versions['takKernelVersion'],
                   commonCommoVersion='1.17.2 patched (public source; not %s)' % versions['commonCommoVersion'])
    out = step.dir / 'templates'
    root = 'Platform/src/main/java-templates/'
    names = sorted(p.relative_to(SRC / root).as_posix() for p in (SRC / root).rglob('*') if p.is_file())
    require(len(names) == 3, 'three java-templates expected')
    for name in names:
        source = step.use('official-template', SRC / root / name)
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expand(source.read_text(), properties, project))
    produced = inventory(out, '.java')
    frozen = {k[len(PROBE + 'generated/templates/'):]: v for k, v in platform_lock()['sources'].items()
              if k.startswith(PROBE + 'generated/templates/')}
    result = compare(produced, frozen)
    require(result['names_equal'], 'template names differ from the expected three')
    step.finish(templates=result, generated=produced, platform_version=platform_version,
                declared_counts={k: len(v) for k, v in declared.items()},
                note='package-info.java is javadoc only and yields no class; its dependency tables state the '
                     'versions actually used and are not claimed to equal Gradle\'s tables')


# --------------------------------------------------------------------------
def resolve_classpath(step, references, kernel):
    """Map specification classpath references, in order, to files here."""
    mapping = {entry['entry']: entry for entry in load(ROOT / 'status/java-classpath.json')['classpath']}
    mapping.update({entry['entry']: entry for entry in load(ROOT / 'status/java-classpath.json')['processorpath']})
    files = client_lock()['files']
    resolved, report = [], []
    for reference in references:
        item = {'reference': reference}
        if reference in OWN_DIRS:
            path = OUT / OWN_DIRS[reference]
            require(path.is_dir(), 'own class directory is missing: %s' % path)
            item.update(path=str(path), kind='fresh class directory of this recipe')
            for name, digest in inventory(path, '.class').items():
                step.inputs[str(path / name)] = {'kind': 'fresh-class', 'sha256': digest}
        elif reference == KERNEL_REF:
            item.update(path=str(step.use('composed-kernel-jar', kernel['jar'])), kind='composed Kernel JAR',
                        lock_digest_equal=sha(kernel['jar']) == files[reference])
        elif reference in (INTEROP_REF, TEXTURE_REF, CONTEXT_REF):
            path = kernel['class_dirs'].get(reference)
            if path is None:
                item.update(path=None, kind='omitted: these classes are inside the composed Kernel JAR')
                report.append(item)
                continue
            item.update(path=str(path), kind='class directory supplied by the Kernel stage')
            for name, digest in inventory(path, '.class').items():
                step.inputs[str(Path(path) / name)] = {'kind': 'kernel-stage-class', 'sha256': digest}
        else:
            entry = mapping.get(reference)
            require(entry and entry['kind'] == 'file', 'no file mapped for classpath entry ' + reference)
            path = step.use('classpath-jar', entry['path'], note=entry['source'])
            equal = sha(path) == files[reference]
            if not equal:
                # Only locally rebuilt project artifacts may differ from the frozen digest.
                require(entry['source'].startswith('project-built'), 'public JAR differs from its pin: ' + reference)
            item.update(path=str(path), kind=entry['source'], lock_digest_equal=equal)
        resolved.append(Path(item['path']))
        report.append(item)
    require(not any(os.pathsep in str(p) for p in resolved), 'classpath entry contains a separator')
    return resolved, report


def kernel_inputs(args):
    """The composed Kernel JAR and what it carries of the three overlay families."""
    require(args.kernel_jar, '--kernel-jar is required for this step')
    jar = Path(args.kernel_jar)
    require(jar.is_file(), 'composed Kernel JAR is missing: %s' % jar)
    with zipfile.ZipFile(jar) as archive:
        names = set(archive.namelist())
    lock = client_lock()
    class_dirs, coverage = {}, {}
    supplied = {INTEROP_REF: args.interop_classes, TEXTURE_REF: args.texture_classes,
                CONTEXT_REF: args.context_classes}
    for reference in (INTEROP_REF, TEXTURE_REF, CONTEXT_REF):
        wanted = set(lock['class_directories'][reference])
        in_jar = wanted & names
        coverage[reference] = {'expected_names': len(wanted), 'names_in_kernel_jar': len(in_jar),
                               'names_missing_from_kernel_jar': sorted(wanted - names)}
        if supplied[reference]:
            path = Path(supplied[reference])
            require(path.is_dir(), 'class directory is missing: %s' % path)
            class_dirs[reference] = path
            coverage[reference]['class_directory'] = str(path)
    return {'jar': jar, 'class_dirs': class_dirs, 'coverage': coverage}


def platform(args):
    require(all(step_done(s) for s in ('prep', 'xjc', 'templates')), 'prep, xjc and templates must be ok first')
    kernel = kernel_inputs(args)
    step = Step('platform')
    javac = jdk(step)
    lock = platform_lock()
    consumers = ROOT / 'P/consumers57'
    sources = []
    for reference in sorted(lock['sources']):
        root, relative = reference.split(':', 1)
        if reference.startswith(PROBE + 'generated/xjc/'):
            path = step.use('fresh-xjc-source', OUT / 'xjc/xjc' / reference[len(PROBE + 'generated/xjc/'):])
        elif reference.startswith(PROBE + 'generated/templates/'):
            path = step.use('fresh-template-source',
                            OUT / 'templates/templates' / reference[len(PROBE + 'generated/templates/'):])
        elif root == 'ww-interop-proof':
            path = step.use('worldwind-consumer-postimage', consumers / relative[len('consumers57/'):],
                            lock['sources'][reference])
        else:
            path = step.use('official-source', SRC / relative[len('5.7/'):], lock['sources'][reference])
        sources.append(path)
    require(len(sources) == 1815, 'the entire 1,815-source Platform set is required')
    for directory, members in lock['source_directories'].items():
        if directory.startswith(READINESS):
            base = SRC / directory[len(READINESS):]
            require(sorted(p.relative_to(base).as_posix() for p in base.rglob('*.java')) == sorted(members),
                    'official source directory membership differs: ' + directory)
    classpath, report = resolve_classpath(step, lock['classpath'], kernel)
    processors, _ = resolve_classpath(step, lock['processorpath'], kernel)
    for name in ('classes', 'generated', 'empty', 'tmp'):
        (step.dir / name).mkdir()
    command = [javac, *JAVAC_FLAGS[:2], '-J-Djava.io.tmpdir=' + str(step.dir / 'tmp'), *JAVAC_FLAGS[2:],
               '-sourcepath', step.dir / 'empty', '-cp', os.pathsep.join(map(str, classpath)),
               '-d', step.dir / 'classes', '-processor', ','.join(lock['processors']),
               '-processorpath', os.pathsep.join(map(str, processors)),
               '-AeventBusIndex=gov.takx.core.event.PlatformEventIndex', '-s', step.dir / 'generated',
               write_args(step.dir / 'sources.args', sources)]
    step.run('platform', command)
    classes = inventory(step.dir / 'classes', '.class')
    generated = inventory(step.dir / 'generated', '.java')
    expected = client_lock()['class_directories'][PROBE + 'platform-classes']
    result = compare(classes, expected)
    result.update(sources=len(sources), generated_sources=sorted(generated),
                  generated_names_equal=sorted(generated) == sorted(lock['expected_generated_source_names']))
    step.finish(verified=result['names_equal'] and result['generated_names_equal'],
                problem='Platform compiled with zero errors but its class or generated-source names differ '
                        'from the expected sets; see receipts/platform.json',
                platform=result, classpath=report, kernel=kernel['coverage'], kernel_jar=str(kernel['jar']),
                kernel_jar_sha256=sha(kernel['jar']))


def aspects(args):
    require(step_done('platform'), 'platform must be ok first')
    kernel = kernel_inputs(args)
    step = Step('aspects')
    jdk(step)
    lock = platform_lock()
    tools = closure(step, 'org.aspectj--aspectjtools-1.9.21.1.jar')
    sources = [step.source(k[len(READINESS):]) for k in sorted(client_lock()['files'])
               if k.startswith(READINESS + 'Platform/src/main/aspectj/') and k.endswith('.aj')]
    require(len(sources) == 4, 'four .aj aspects expected')
    classpath, report = resolve_classpath(step, lock['classpath'], kernel)
    # Platform/build.gradle: compileAspectj sees the compileJava output for type
    # resolution only, with empty inpath and aspectpath, so nothing is woven.
    classpath = [OUT / 'platform/classes'] + classpath
    for name, digest in inventory(OUT / 'platform/classes', '.class').items():
        step.inputs[str(OUT / 'platform/classes' / name)] = {'kind': 'fresh-class', 'sha256': digest}
    (step.dir / 'classes').mkdir()
    command = [JDK / 'bin/java', '-Xmx768m', '-XX:ActiveProcessorCount=1', '-cp', tools,
               'org.aspectj.tools.ajc.Main', '-17', '-encoding', 'UTF-8', '-XhasMember', '-Xlint:ignore',
               '-outxmlfile', 'aop/aop.xml', '-cp', os.pathsep.join(map(str, classpath)),
               '-d', step.dir / 'classes', *sources]
    step.run('ajc', command)
    classes = inventory(step.dir / 'classes', '.class')
    result = compare(classes, client_lock()['class_directories'][PROBE + 'aspect-classes'])
    aop = step.dir / 'classes/aop/aop.xml'
    require(aop.is_file(), 'ajc did not write aop/aop.xml')
    result.update(sources=len(sources), aop_xml_sha256=sha(aop),
                  aop_xml_equals_frozen_informational=sha(aop) == client_lock()['files'][
                      PROBE + 'aspect-classes/aop/aop.xml'])
    step.finish(verified=result['names_equal'], problem='aspect class names differ from the expected eight',
                aspects=result, kernel_jar_sha256=sha(kernel['jar']),
                classpath_head='platform/classes, then the Platform classpath',
                weaving='none: no -inpath and no -aspectpath; load-time weaving reads aop/aop.xml')


def client(args):
    require(step_done('aspects'), 'aspects must be ok first')
    kernel = kernel_inputs(args)
    step = Step('client')
    javac = jdk(step)
    lock = client_lock()
    consumers = ROOT / 'P/consumers57'
    sources = []
    for reference in sorted(lock['sources']):
        root, relative = reference.split(':', 1)
        if root == 'ww-interop-proof':
            path = step.use('worldwind-consumer-postimage', consumers / relative[len('consumers57/'):],
                            lock['sources'][reference])
        else:
            path = step.use('official-source', SRC / relative[len('5.7/'):], lock['sources'][reference])
        sources.append(path)
    require(len(sources) == 1678, 'all 1,678 Client sources are required')
    tree = SRC / 'Client/src/main/java'
    require(sorted(p.relative_to(tree).as_posix() for p in tree.rglob('*.java'))
            == sorted(lock['official_client_sources']), 'official Client source membership differs')
    classpath, report = resolve_classpath(step, lock['classpath'], kernel)
    processors, _ = resolve_classpath(step, lock['processorpath'], kernel)
    for name in ('classes', 'generated', 'empty', 'tmp'):
        (step.dir / name).mkdir()
    command = [javac, *JAVAC_FLAGS[:2], '-J-Djava.io.tmpdir=' + str(step.dir / 'tmp'), *JAVAC_FLAGS[2:],
               '-sourcepath', step.dir / 'empty', '-cp', os.pathsep.join(map(str, classpath)),
               '-d', step.dir / 'classes',
               '-processor', 'org.greenrobot.eventbus.annotationprocessor.EventBusAnnotationProcessor',
               '-processorpath', os.pathsep.join(map(str, processors)),
               '-AeventBusIndex=gov.takx.core.event.ClientEventIndex', '-s', step.dir / 'generated',
               write_args(step.dir / 'sources.args', sources)]
    step.run('client', command)
    classes = inventory(step.dir / 'classes', '.class')
    generated = inventory(step.dir / 'generated', '.java')
    result = compare(classes, set(lock['expected_class_names']))
    result.update(sources=len(sources), generated_sources=sorted(generated),
                  generated_names_equal=sorted(generated) == sorted(lock['expected_generated_sources']),
                  hash_equal_informational='not available: the specification lists Client class names only')
    step.finish(verified=result['names_equal'] and result['generated_names_equal'],
                problem='Client compiled with zero errors but its class or generated-source names differ '
                        'from the expected sets; see receipts/client.json',
                client=result, classpath=report, kernel=kernel['coverage'], kernel_jar_sha256=sha(kernel['jar']))


# --------------------------------------------------------------------------
def write_jar(step, path, manifest, trees):
    """A deterministic JAR: manifest first, sorted entries, fixed timestamps."""
    entries = {}
    for label, tree in trees:
        tree = Path(tree)
        for file in sorted(p for p in tree.rglob('*') if p.is_file()):
            name = file.relative_to(tree).as_posix()
            require(name != 'META-INF/MANIFEST.MF', 'unexpected manifest in ' + str(tree))
            if name in entries:
                require(sha(file) == sha(entries[name][1]), 'conflicting duplicate JAR entry ' + name)
                continue
            entries[name] = (label, file)
    lines = ['Manifest-Version: 1.0'] + ['%s: %s' % item for item in manifest.items()]
    require(all(len(line.encode()) <= 70 for line in lines), 'manifest line longer than 70 bytes')
    stamp = (1980, 2, 1, 0, 0, 0)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        def add(name, data):
            info = zipfile.ZipInfo(name, stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
        add('META-INF/MANIFEST.MF', ('\r\n'.join(lines) + '\r\n\r\n').encode())
        for name in sorted(entries):
            add(name, entries[name][1].read_bytes())
    counts = {}
    for label, _ in entries.values():
        counts[label] = counts.get(label, 0) + 1
    return {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size, 'entries': counts,
            'manifest': manifest}


def lfs_pointers(tree):
    found = []
    for file in Path(tree).rglob('*'):
        if file.is_file() and file.stat().st_size < 1024:
            if file.read_bytes().startswith(b'version https://git-lfs.github.com/spec/'):
                found.append(str(file))
    return found


def package():
    require(all(step_done(s) for s in ('prep', 'platform', 'aspects', 'client')), 'all compile steps must be ok')
    step = Step('package')
    jdk(step)
    base = step.use('official-source', SRC / 'platformVersion.txt')
    # A local, non-release Gradle build names its artifacts <base>-SNAPSHOT
    # (BuildSupportPlugin: buildVersion = isRelease ? baseVersion : asSnapshot()).
    version = base.read_text().strip() + '-SNAPSHOT'
    common = {'Implementation-Version': version,
              'Build-Date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
              'Build-Jdk': '17.0.21', 'Build-Number': REVISION, 'Build-Branch': 'release/5.7',
              'Built-By': 'takx', 'TAKX-Flavor': 'CIV',
              'X-Prototype-Builder': 'uds-takserver takx/desktop-prototype',
              'X-Prototype-Kernel': 'public 8.30.5 plus project patches',
              'X-Prototype-CommonCommo': 'public 1.17.2 patched'}
    resources = {name: SRC / name / 'src/main/resources' for name in ('Bootstrap', 'Platform', 'Client')}
    pointers = [p for tree in resources.values() for p in lfs_pointers(tree)]
    require(not pointers, 'Git LFS pointer files among the resources; fetch their bytes first: %s' % pointers)
    resource_counts = {}
    for name, tree in resources.items():
        files = inventory(tree)
        resource_counts[name] = len(files)
        for relative, digest in files.items():
            step.inputs[str(tree / relative)] = {'kind': 'official-resource', 'sha256': digest}
    for directory in ('prep/preferences-classes', 'prep/bootstrap-classes', 'prep/processmanager-classes',
                      'platform/classes', 'aspects/classes', 'client/classes'):
        for relative, digest in inventory(OUT / directory).items():
            step.inputs[str(OUT / directory / relative)] = {'kind': 'fresh-class', 'sha256': digest}
    load_dir, boot_dir = step.dir / 'load', step.dir / 'boot'
    load_dir.mkdir()
    boot_dir.mkdir()
    (step.dir / 'unsigned').mkdir()
    jars = {}
    # Bootstrap/build.gradle: the agent attributes of the boot JAR.
    agent = dict(common)
    agent.update({'Agent-Class': 'gov.takx.bootstrap.AopClassTransformer',
                  'Premain-Class': 'gov.takx.bootstrap.AopClassTransformer', 'Can-Redefine-Classes': 'true'})
    unsigned = step.dir / 'unsigned' / ('bootstrap-%s.jar' % version)
    jars['bootstrap-unsigned'] = write_jar(step, unsigned, agent, [('classes', OUT / 'prep/bootstrap-classes'),
                                                                   ('resources', resources['Bootstrap'])])
    # preferencesJar is a plain Jar task; configureManifest only touches 'jar'.
    jars['preferences-factory'] = write_jar(step, boot_dir / ('preferences-factory-%s.jar' % version), {},
                                            [('classes', OUT / 'prep/preferences-classes')])
    jars['platform'] = write_jar(step, load_dir / ('platform-%s.jar' % version), common,
                                 [('classes', OUT / 'platform/classes'), ('aspects', OUT / 'aspects/classes'),
                                  ('resources', resources['Platform'])])
    jars['client'] = write_jar(step, load_dir / ('client-%s.jar' % version), common,
                               [('classes', OUT / 'client/classes'), ('resources', resources['Client'])])
    jars['processmanager'] = write_jar(step, load_dir / ('processmanager-%s.jar' % version), common,
                                       [('classes', OUT / 'prep/processmanager-classes')])
    # Outside GitLab the official build signs the bootstrap JAR with the
    # developer key tracked in the public tree. Alias and passwords are read
    # from Bootstrap/build.gradle at run time and are not repeated here.
    gradle = step.source('Bootstrap/build.gradle').read_text()
    sign = dict(re.findall(r"(?m)^\s*(alias|storetype|keystore|storepass|keypass):\s*'([^']*)'", gradle))
    require(set(sign) == {'alias', 'storetype', 'keystore', 'storepass', 'keypass'}, 'signjar settings not found')
    keystore = step.use('official-developer-keystore', SRC / 'Bootstrap' / sign['keystore'])
    signed = load_dir / ('bootstrap-%s.jar' % version)
    shutil.copyfile(unsigned, signed)
    signing = {'attempted': True}
    try:
        env = dict(os.environ, TAKX_SIGN_STOREPASS=sign['storepass'], TAKX_SIGN_KEYPASS=sign['keypass'],
                   LC_ALL='C.UTF-8', TZ='UTC')
        for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS'):
            env.pop(key, None)
        with open(step.dir / 'jarsigner.log', 'wb') as stream:
            code = subprocess.run([str(JDK / 'bin/jarsigner'), '-keystore', str(keystore),
                                   '-storetype', sign['storetype'], '-storepass:env', 'TAKX_SIGN_STOREPASS',
                                   '-keypass:env', 'TAKX_SIGN_KEYPASS', str(signed), sign['alias']],
                                  env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                  timeout=300).returncode
        signing['exit'] = code
        if code == 0:
            with open(step.dir / 'jarsigner-verify.log', 'wb') as stream:
                signing['verify_exit'] = subprocess.run(
                    [str(JDK / 'bin/jarsigner'), '-verify', str(signed)], env=env, stdin=subprocess.DEVNULL,
                    stdout=stream, stderr=subprocess.STDOUT, timeout=300).returncode
    except (OSError, subprocess.SubprocessError) as error:
        signing['error'] = repr(error)
    signing['signed'] = signing.get('exit') == 0 and signing.get('verify_exit') == 0
    if signing['signed']:
        jars['bootstrap'] = {'path': str(signed), 'sha256': sha(signed), 'bytes': signed.stat().st_size,
                             'signed_with': 'the developer key of the official tree (Bootstrap/dev_key.jks), '
                                            'as the official local build does',
                             'manifest': agent}
    else:
        # Fall back to the unsigned JAR and say so; Bootstrap then ignores the
        # flavor attribute and defaults to CIV.
        shutil.copyfile(unsigned, signed)
        jars['bootstrap'] = dict(jars['bootstrap-unsigned'], path=str(signed), signed_with=None)
    # Every class of every JAR really is the compiler output of this recipe.
    for name, directories in (('platform', ['platform/classes', 'aspects/classes']), ('client', ['client/classes']),
                              ('processmanager', ['prep/processmanager-classes']),
                              ('bootstrap', ['prep/bootstrap-classes'])):
        with zipfile.ZipFile(jars[name]['path']) as archive:
            inside = {n for n in archive.namelist() if n.endswith('.class')}
        wanted = {n for d in directories for n in inventory(OUT / d, '.class')}
        require(inside == wanted, '%s JAR class membership differs from the compiler output' % name)
        jars[name]['classes'] = len(inside)
    step.finish(version=version, jars=jars, signing=signing, resource_counts=resource_counts,
                lfs_pointer_resources=0,
                layout='load/ holds bootstrap, platform, client and processmanager; boot/ holds '
                       'preferences-factory; jboss-modules for boot/ is in P/takx57-runtime-jars')


# --------------------------------------------------------------------------
def lock():
    steps = ['prep', 'xjc', 'templates', 'platform', 'aspects', 'client', 'package']
    require(all(step_done(s) for s in steps), 'every step must be ok before the lock is written')
    inputs, outputs, summary = {}, {}, {}

    def logical(path):
        path = str(path)
        for prefix, name in ((str(OUT) + '/', 'takx57:'), (str(SRC) + '/', 'takx-5.7-source:'),
                             (str(ROOT / 'P') + '/', 'proto-P:'), (str(ROOT / 'jars') + '/', 'proto-jars:'),
                             (str(JDK) + '/', 'temurin-17.0.21+9:'), (str(ROOT) + '/', 'proto-root:')):
            if path.startswith(prefix):
                return name + path[len(prefix):]
        return path
    for name in steps:
        receipt = load(OUT / 'receipts' / (name + '.json'))
        for path, entry in receipt['inputs'].items():
            # Classes this recipe compiled are outputs, not inputs.
            if entry['kind'].startswith('fresh-'):
                continue
            key = logical(path)
            require(inputs.get(key, entry)['sha256'] == entry['sha256'], 'one input has two digests: ' + key)
            inputs[key] = entry
        summary[name] = {k: receipt[k] for k in ('modules', 'xjc', 'templates', 'platform', 'aspects', 'client')
                         if k in receipt}
        for value in summary[name].values():
            for item in (value.values() if name == 'prep' else [value]):
                for noisy in ('missing_names', 'unexpected_names', 'generated_sources'):
                    if isinstance(item.get(noisy), list) and len(item[noisy]) > 20:
                        item[noisy] = '%d names; see the step receipt' % len(item[noisy])
    package_receipt = load(OUT / 'receipts/package.json')
    for name, jar in package_receipt['jars'].items():
        outputs[logical(jar['path'])] = jar['sha256']
    runtime = ROOT / 'P/takx57-runtime-jars/manifest.json'
    grouped = {}
    for key in sorted(inputs):
        grouped.setdefault(inputs[key]['kind'], {})[key] = inputs[key]['sha256']
    # The tracked lock holds inputs only. Outputs carry the build date in their
    # manifests, so they and the comparison results go to result.json.
    document = {
        'schema': 1,
        'scope': 'TAKX 5.7 desktop prototype: every input digest actually consumed by the fresh Bootstrap, '
                 'ProcessManager, XJC, template, Platform, aspect and Client compilation and by packaging',
        'official_revision': REVISION, 'actual': ACTUAL,
        'specification': {'takx/client-compiler/inputs.lock.json': sha(SPEC / 'inputs.lock.json'),
                          'takx/client-compiler/platform-inputs.lock.json': sha(SPEC / 'platform-inputs.lock.json')},
        'roots': {'takx57': 'P/takx57, the output of this recipe',
                  'takx-5.7-source': 'the official TAKX checkout at official_revision',
                  'proto-P': 'outputs of the other desktop-prototype stages; locally rebuilt, so their '
                             'digests are those of the run that wrote this lock',
                  'proto-jars': 'public JARs fetched and verified by fetch.sh',
                  'temurin-17.0.21+9': 'the Temurin JDK of the toolchain image',
                  'proto-root': 'the desktop-prototype work root'},
        'input_counts': {kind: len(files) for kind, files in sorted(grouped.items())},
        'inputs': grouped}
    dump(OUT / 'inputs.lock.json', document)
    dump(OUT / 'result.json', {
        'official_revision': REVISION, 'actual': ACTUAL, 'inputs_lock_sha256': sha(OUT / 'inputs.lock.json'),
        'recipe_sha256': sha(__file__), 'outputs': outputs, 'results': summary,
        'compiler_errors': 0, 'jar_version': package_receipt['version'], 'signing': package_receipt['signing'],
        'runtime_jars_manifest_sha256': sha(runtime) if runtime.exists() else None,
        'gui_launched': False, 'native_library_loaded': False, 'private_sdk_equivalence': False})
    print('lock: %d inputs, %d output JARs' % (len(inputs), len(outputs)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('step', choices=['prep', 'xjc', 'templates', 'platform', 'aspects', 'client',
                                         'package', 'lock'])
    parser.add_argument('--kernel-jar', help='the composed Kernel JAR (platform, aspects, client)')
    parser.add_argument('--interop-classes', help='kernel-client-interop class directory, if not in the Kernel JAR')
    parser.add_argument('--texture-classes', help='texture overlay class directory, if not in the Kernel JAR')
    parser.add_argument('--context-classes', help='render-context class directory, if not in the Kernel JAR')
    args = parser.parse_args()
    require(sha(SPEC / 'inputs.lock.json') and (SRC / 'Platform').is_dir(), 'specification or source is missing')
    try:
        if args.step in ('platform', 'aspects', 'client'):
            globals()[args.step](args)
        else:
            globals()[args.step]()
    except Failure as error:
        print('FAILED %s: %s' % (args.step, error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
