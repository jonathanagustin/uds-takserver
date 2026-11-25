#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Resolve the runtime JAR closure the TAKX 5.7 assembler needs.

Runs inside the takx-desktop-proto:toolchain image (see ../stages/client.sh).
The dependency declarations are read from the official 5.7 Gradle files
(gradle/versions.gradle, platformDependencies.gradle, clientDependencies.gradle,
binCreator.gradle and Bootstrap/build.gradle); a POM is generated from them and
Apache Maven resolves the runtime scope from Maven Central and the JogAmp
repository only, with strict checksums. Nothing is ever requested from
artifacts.tak.gov: the private coordinates are listed in the manifest with the
public substitute this prototype builds instead.

Versions of artifacts that are part of the compile closure are managed to the
closure version (the 129 JARs the specification lock pins), so compile and
runtime agree. Maven mediates the remaining versions with nearest-wins, which
is not Gradle's highest-wins; the manifest records what was actually selected.

Output: P/takx57-runtime-jars/jars/*.jar and manifest.json (coordinates,
sha256, repository, whether the same bytes are in the compile closure).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
REPO = Path(os.environ.get('TAKX_PROTO_REPO', Path(__file__).resolve().parents[3]))
JDK = Path('/tools/temurin-17.0.21_9/jdk-17.0.21+9')
SRC = ROOT / 'src/takx57'
OUT = ROOT / 'P/takx57-runtime-jars'
CACHE = ROOT / 'cache-client'
MAVEN_VERSION = '3.9.9'
MAVEN_URL = ('https://repo1.maven.org/maven2/org/apache/maven/apache-maven/%s/apache-maven-%s-bin.tar.gz'
             % (MAVEN_VERSION, MAVEN_VERSION))
# Published identically on repo1.maven.org and archive.apache.org.
MAVEN_SHA512 = ('a555254d6b53d267965a3404ecb14e53c3827c09c3b94b5678835887ab404556'
                'bfaf78dcfe03ba76fa2508649dca8531c74bca4d5846513522404d48e8c4ac8b')
CENTRAL = 'https://repo1.maven.org/maven2'
JOGAMP = 'https://jogamp.org/deployment/maven'
# Coordinates TAKX 5.7 declares that exist only on the private TAK.gov
# Artifactory. They are never requested; each names its public substitute.
PRIVATE = {
    'gov.nasa:worldwind': 'P/worldwind-interop/uds-takserver-worldwind-java-interop.jar '
                          '(takx/worldwind-java --interop, NASA v2.2.1; not the private 2.2.1-TAKX-4)',
    'gov.tak.kernel:takkernel-all': 'the composed Kernel JAR of the Kernel stage (public Kernel 8.30.5 plus '
                                    'project patches; not 8.57.11) and its dependency JARs in '
                                    'P/kernel-java/classpath',
    'gov.tak.kernel:takkernel-engine-rt-linux64': 'the native libraries inside the composed Kernel JAR '
                                                  '(public 8.30.5 natives; not 8.57.11)',
    'gov.tak.kernel:takkernel-engine-rt-win64': 'none: Linux amd64 prototype only',
    'jcommoncommo:jcommoncommo': 'P/commoncommo/install/java/jcommoncommo.jar (public CommonCommo 1.17.2 '
                                 'patched; not 1.20.0)',
    'net.java.dev:colorchooser': 'P/ui-original/uds-takserver-ui-colorchooser-author-1.5.jar (takx/ui-java)',
    'org.dockingframes:docking-frames-common': 'P/ui-original/uds-takserver-ui-docking-common.jar (takx/ui-java)',
    'org.dockingframes:docking-frames-core': 'P/ui-original/uds-takserver-ui-docking-core.jar (takx/ui-java)',
    'raven.toast:swing-toast-notifications': 'P/ui-original/uds-takserver-ui-toast-1.0.3.jar (takx/ui-java)',
    'org.netbeans:wizard': 'P/ui-wizard-reference/uds-takserver-ui-wizard-reference.jar (takx/ui-java)',
    'assets:tak-assets': 'no JAR: public atak-civ asset files are staged by the assembly stage',
    'gov.raptor.resources:border-shapefiles': 'none: absent from the prototype',
    'postgresql:postgresql-linux': 'none: not needed, the default database is hsqldb',
}
JOGAMP_GROUPS = ('org.jogamp.jogl', 'org.jogamp.gluegen', 'org.jogamp.jocl')
# 'exclude module:' rules of the official dependency scripts.
EXCLUSIONS = {'com.google.guava:guava': [('com.google.guava', 'listenablefuture'),
                                         ('com.google.j2objc', 'j2objc-annotations')],
              # svgSalamander is published on JitPack only, which this resolver does
              # not use; the prototype builds it from the public tag instead.
              'org.mapsforge:mapsforge-map-awt': [('org.apache.ant', 'ant'),
                                                  ('com.github.blackears', 'svgSalamander')]}
TRANSITIVE_SUBSTITUTES = [
    {'coordinates': 'com.github.blackears:svgSalamander:v1.1.1', 'declared_in': 'transitive of '
     'org.mapsforge:mapsforge-map-awt', 'status': 'public-source-substitute',
     'reason': 'published on JitPack only, not on Maven Central or JogAmp',
     'public_substitute': 'P/ui-original/uds-takserver-ui-salamander-tag-v1.1.1.jar (takx/ui-java)',
     'downloaded': False}]


def fail(message):
    print('FAILED runtime-jars: ' + message, file=sys.stderr)
    sys.exit(1)


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def maven_home():
    """Fetch (once) and unpack the pinned Apache Maven distribution."""
    archive = CACHE / 'maven' / ('apache-maven-%s-bin.tar.gz' % MAVEN_VERSION)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists() or digest(archive, 'sha512') != MAVEN_SHA512:
        temporary = archive.with_suffix('.part')
        with urllib.request.urlopen(MAVEN_URL, timeout=120) as response, open(temporary, 'wb') as stream:
            shutil.copyfileobj(response, stream)
        if digest(temporary, 'sha512') != MAVEN_SHA512:
            fail('Apache Maven archive differs from its pinned SHA-512')
        os.replace(temporary, archive)
    home = CACHE / 'maven' / ('apache-maven-' + MAVEN_VERSION)
    if not (home / 'bin/mvn').exists():
        with tarfile.open(archive) as tar:
            tar.extractall(CACHE / 'maven', filter='data')
    return home, archive


def declared():
    """Every dependency the official scripts declare for the runtime."""
    versions = dict(re.findall(r"(?m)^\s*(\w+)\s*=\s*'([^']*)'", (SRC / 'gradle/versions.gradle').read_text()))
    found, sources = [], {}
    files = {'gradle/platformDependencies.gradle': None, 'gradle/clientDependencies.gradle': None,
             'Bootstrap/build.gradle': None}
    pattern = re.compile(r'(?m)^\s*(api|implementation|runtimeOnly|bootLibs)\s*\(?\s*"([^"]+)"')
    for name in files:
        text = (SRC / name).read_text()
        sources[name] = digest(SRC / name)
        for configuration, coordinate in pattern.findall(text):
            parts = [re.sub(r'\$\{?(\w+)\}?', lambda m: versions.get(m.group(1), m.group(0)), p)
                     for p in coordinate.split(':')]
            if '$' in ''.join(parts):
                # takkernel-engine-rt-${platform}64 is a loop over win and linux.
                if parts[0] == 'gov.tak.kernel':
                    for platform in ('win', 'linux'):
                        found.append((name, configuration, parts[0], parts[1].replace('${platform}', platform),
                                      parts[2], None))
                    continue
                fail('unresolved Gradle variable in ' + coordinate)
            # "com.sun.xml.bind:jaxb-ri" is declared with 'strictly jaxbVersion'.
            version = parts[2] if len(parts) > 2 else versions['jaxbVersion']
            found.append((name, configuration, parts[0], parts[1], version, parts[3] if len(parts) > 3 else None))
    # gradle/binCreator.gradle:62-75: the platform natives and the Bootstrap
    # weaver. Only the Linux amd64 classifiers are taken.
    name = 'gradle/binCreator.gradle'
    text = (SRC / name).read_text()
    sources[name] = digest(SRC / name)
    sources['gradle/versions.gradle'] = digest(SRC / 'gradle/versions.gradle')
    for needle in ('natives-linux-amd64', 'org.jogamp.jogl:jogl-all:$joglVersion:$cls',
                   'org.jogamp.gluegen:gluegen-rt:$joglVersion:$cls', 'org.jogamp.jocl:jocl:$joclVersion:$cls',
                   'org.bytedeco:ffmpeg:$ffmpegVersion:$cls', "'linux-x86_64'",
                   'org.aspectj:aspectjweaver:$aspectjVersion'):
        if needle not in text:
            fail('binCreator.gradle no longer declares ' + needle)
    found += [(name, 'runtimeOnly', 'org.jogamp.jogl', 'jogl-all', versions['joglVersion'], 'natives-linux-amd64'),
              (name, 'runtimeOnly', 'org.jogamp.gluegen', 'gluegen-rt', versions['joglVersion'],
               'natives-linux-amd64'),
              (name, 'runtimeOnly', 'org.jogamp.jocl', 'jocl', versions['joclVersion'], 'natives-linux-amd64'),
              (name, 'runtimeOnly', 'org.bytedeco', 'ffmpeg', versions['ffmpegVersion'], 'linux-x86_64'),
              (name, 'implementation', 'org.aspectj', 'aspectjweaver', versions['aspectjVersion'], None)]
    return found, sources, versions


def closure_pins():
    """group:artifact -> version for every public JAR of the compile closure.

    The closure names its Maven JARs group--artifact-version.jar; the other
    public JARs carry their repository URL in the fetch record. The digests
    are those of the specification lock.
    """
    lock = json.load(open(REPO / 'takx/client-compiler/inputs.lock.json'))
    pins, digests = {}, {}
    prefix = 'takx-client-compiler-closure:maven-public/'
    for reference, sha256 in lock['files'].items():
        if reference.endswith('.jar'):
            digests[sha256] = reference
        if not reference.startswith(prefix):
            continue
        match = re.match(r'^(.+)--(.+?)-(\d.*)\.jar$', reference[len(prefix):])
        if not match:
            fail('cannot read coordinates of closure JAR ' + reference)
        pins['%s:%s' % (match.group(1), match.group(2))] = match.group(3)
    for item in json.load(open(ROOT / 'status/fetch.json'))['inputs']:
        if item['kind'] != 'client-compiler-jar' or not str(item.get('source')).startswith('https://'):
            continue
        match = re.match(r'https://[^/]+/(?:maven2|deployment/maven)/(.+)/([^/]+)/([^/]+)/[^/]+\.jar$',
                         item['source'])
        if not match:
            fail('cannot read coordinates of closure JAR ' + item['name'])
        pins.setdefault('%s:%s' % (match.group(1).replace('/', '.'), match.group(2)), match.group(3))
    return pins, digests


def write_pom(path, dependencies, pins):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<project xmlns="http://maven.apache.org/POM/4.0.0">', ' <modelVersion>4.0.0</modelVersion>',
             ' <groupId>dev.uds.takserver.prototype</groupId>', ' <artifactId>takx57-runtime-closure</artifactId>',
             ' <version>1</version>', ' <packaging>pom</packaging>', ' <repositories>']
    for identifier, url in (('central', CENTRAL), ('jogamp', JOGAMP)):
        lines += ['  <repository><id>%s</id><url>%s</url>' % (identifier, url),
                  '   <releases><enabled>true</enabled><checksumPolicy>fail</checksumPolicy></releases>',
                  '   <snapshots><enabled>false</enabled></snapshots></repository>']
    lines += [' </repositories>', ' <dependencyManagement><dependencies>']
    for key in sorted(pins):
        group, artifact = key.split(':')
        lines.append('  <dependency><groupId>%s</groupId><artifactId>%s</artifactId><version>%s</version>'
                     '</dependency>' % (escape(group), escape(artifact), escape(pins[key])))
    lines += [' </dependencies></dependencyManagement>', ' <dependencies>']
    for group, artifact, version, classifier in sorted(dependencies, key=lambda d: tuple(x or '' for x in d)):
        entry = '  <dependency><groupId>%s</groupId><artifactId>%s</artifactId><version>%s</version>' % (
            escape(group), escape(artifact), escape(version))
        if classifier:
            entry += '<classifier>%s</classifier>' % escape(classifier)
        if artifact == 'jaxb-ri':
            entry += '<type>pom</type>'
        entry += '<scope>runtime</scope>'
        if '%s:%s' % (group, artifact) in EXCLUSIONS:
            entry += '<exclusions>' + ''.join(
                '<exclusion><groupId>%s</groupId><artifactId>%s</artifactId></exclusion>' % pair
                for pair in EXCLUSIONS['%s:%s' % (group, artifact)]) + '</exclusions>'
        lines.append(entry + '</dependency>')
    lines += [' </dependencies>', '</project>', '']
    path.write_text('\n'.join(lines))


def main():
    started = time.time()
    truststore = os.environ.get('TAKX_JAVA_TRUSTSTORE')
    if OUT.exists():
        n = 1
        while (ROOT / 'failed' / ('takx57-runtime-jars-%d' % n)).exists():
            n += 1
        (ROOT / 'failed').mkdir(exist_ok=True)
        shutil.move(str(OUT), str(ROOT / 'failed' / ('takx57-runtime-jars-%d' % n)))
    work = OUT / 'work'
    work.mkdir(parents=True)
    home, archive = maven_home()
    found, gradle_sources, versions = declared()
    pins, closure_digests = closure_pins()
    private, requested, direct = [], set(), {}
    for source, configuration, group, artifact, version, classifier in found:
        key = '%s:%s' % (group, artifact)
        if key in PRIVATE:
            item = {'coordinates': '%s:%s' % (key, version), 'declared_in': source,
                    'configuration': configuration, 'status': 'private-with-public-substitute',
                    'public_substitute': PRIVATE[key], 'downloaded': False}
            if not any(seen['coordinates'] == item['coordinates'] for seen in private):
                private.append(item)
            continue
        if group.startswith(('gov.tak', 'gov.nasa', 'gov.raptor')):
            fail('undeclared private coordinate ' + key)
        # The compile closure selected its own version of a few libraries
        # (README of takx/client-compiler); the closure version wins.
        selected = pins.get(key, version)
        requested.add((group, artifact, selected, classifier))
        direct.setdefault('%s:%s%s' % (key, selected, ':' + classifier if classifier else ''), []).append(
            {'declared_in': source, 'configuration': configuration, 'declared_version': version})
    for key in ('assets:tak-assets', 'gov.raptor.resources:border-shapefiles', 'postgresql:postgresql-linux'):
        private.append({'coordinates': key, 'declared_in': 'gradle/binCreator.gradle or deployment/build.gradle',
                        'status': 'private-with-public-substitute', 'public_substitute': PRIVATE[key],
                        'downloaded': False})
    write_pom(work / 'pom.xml', requested, pins)
    repository = CACHE / 'm2'
    (work / 'settings.xml').write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">\n'
        ' <!-- No mirrors, no credentials, no proxy. TLS is verified against the JDK roots plus the\n'
        '      mounted CA bundle through the trust store named in MAVEN_OPTS. -->\n'
        ' <localRepository>%s</localRepository>\n <interactiveMode>false</interactiveMode>\n</settings>\n'
        % escape(str(repository)))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('JAVA_', 'MAVEN_', '_JAVA'))}
    env['JAVA_HOME'] = str(JDK)
    options = ['-Xmx512m', '-XX:ActiveProcessorCount=1']
    if truststore:
        options += ['-Djavax.net.ssl.trustStore=' + truststore, '-Djavax.net.ssl.trustStorePassword=changeit',
                    '-Djavax.net.ssl.trustStoreType=PKCS12']
    env['MAVEN_OPTS'] = ' '.join(options)
    listing = work / 'resolved.txt'
    command = [str(home / 'bin/mvn'), '-B', '-ntp', '-C', '-s', str(work / 'settings.xml'), '-f',
               str(work / 'pom.xml'), 'org.apache.maven.plugins:maven-dependency-plugin:3.8.1:list',
               '-DincludeScope=runtime', '-DoutputAbsoluteArtifactFilename=true', '-DoutputFile=' + str(listing),
               '-Dsort=true']
    with open(work / 'maven.log', 'wb') as stream:
        code = subprocess.run(command, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                              stderr=subprocess.STDOUT, timeout=3000).returncode
    if code != 0 or not listing.exists():
        fail('Maven exit %s; see %s' % (code, work / 'maven.log'))
    jars = OUT / 'jars'
    jars.mkdir()
    entries = []
    for line in listing.read_text().splitlines():
        # group:artifact:type[:classifier]:version:scope:/absolute/path[ -- module ...]
        match = re.match(r'^\s+(\S+?):(\S+?):(\w+)(?::([\w.-]+))?:([^:\s]+):(\w+):(/\S+)', line)
        if not match:
            continue
        group, artifact, kind, classifier, version, scope, path = match.groups()
        if kind != 'jar':
            continue
        path = Path(path)
        name = '%s--%s-%s%s.jar' % (group, artifact, version, '-' + classifier if classifier else '')
        shutil.copyfile(path, jars / name)
        origin = None
        marker = path.parent / '_remote.repositories'
        if marker.exists():
            hit = re.search(r'(?m)^%s>(\w*)=' % re.escape(path.name), marker.read_text())
            origin = {'central': CENTRAL, 'jogamp': JOGAMP}.get(hit.group(1)) if hit else None
        if origin is None:
            fail('cannot tell which repository served ' + name)
        sha256 = digest(jars / name)
        coordinates = '%s:%s:%s%s' % (group, artifact, version, ':' + classifier if classifier else '')
        relative = '%s/%s/%s/%s' % (group.replace('.', '/'), artifact, version, path.name)
        entries.append({'coordinates': coordinates, 'file': 'jars/' + name, 'sha256': sha256,
                        'bytes': (jars / name).stat().st_size, 'scope': scope, 'repository': origin,
                        'url': '%s/%s' % (origin, relative),
                        'same_bytes_in_compile_closure': closure_digests.get(sha256),
                        'directly_declared': direct.get(coordinates, [])})
    if not entries:
        fail('Maven resolved nothing')
    for group in JOGAMP_GROUPS:
        if not any(e['coordinates'].startswith(group + ':') and 'natives-linux-amd64' in e['coordinates']
                   for e in entries):
            fail('no linux-amd64 natives resolved for ' + group)
    missing = [k for k in direct if not any(e['coordinates'] == k for e in entries)
               and not k.startswith('com.sun.xml.bind:jaxb-ri:')]
    if missing:
        fail('directly declared artifacts were not resolved: %s' % missing)
    manifest = {
        'schema': 1,
        'scope': 'runtime JAR closure for assembling TAKX 5.7 (official 0b59a9bb) in the desktop prototype',
        'resolver': {'tool': 'Apache Maven ' + MAVEN_VERSION, 'archive_url': MAVEN_URL,
                     'archive_sha512': MAVEN_SHA512, 'archive_sha256': digest(archive),
                     'strict_checksums': True, 'repositories': [CENTRAL, JOGAMP],
                     'version_mediation': 'compile-closure versions are managed; otherwise Maven nearest-wins, '
                                          'which is not Gradle highest-wins',
                     'pom_sha256': digest(work / 'pom.xml'), 'settings_sha256': digest(work / 'settings.xml'),
                     'log_sha256': digest(work / 'maven.log')},
        'declaration_sources': gradle_sources,
        'counts': {'jars': len(entries),
                   'same_bytes_in_compile_closure': sum(1 for e in entries if e['same_bytes_in_compile_closure']),
                   'runtime_only_additions': sum(1 for e in entries if not e['same_bytes_in_compile_closure']),
                   'private_not_downloaded': len(private)},
        'notes': [
            'Nothing was requested from artifacts.tak.gov.',
            'The official scripts exclude org.glassfish.jaxb:jaxb-runtime from api, implementation and '
            'runtimeOnly; Maven has no global exclusion, so a jaxb-runtime entry here is one the assembler '
            'may leave out.',
            'Only the Linux amd64 classifiers of the JogAmp and FFmpeg natives were declared; jocl-main is an '
            'umbrella POM and may add natives of other platforms.',
            'The Kernel dependency JARs (mil-sym, kxml2, httpclient, gdal, jassimp and others) are not here: '
            'they are the frozen classpath of P/kernel-java/classpath.'],
        'jars': sorted(entries, key=lambda e: e['coordinates']),
        'private_with_public_substitute': private,
        'transitive_substitutes': TRANSITIVE_SUBSTITUTES,
        'seconds': round(time.time() - started, 1)}
    temporary = OUT / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')
    os.replace(temporary, OUT / 'manifest.json')
    print('runtime-jars ok: %d JARs (%d beyond the compile closure), %d private coordinates not downloaded'
          % (len(entries), manifest['counts']['runtime_only_additions'], len(private)))


if __name__ == '__main__':
    main()
