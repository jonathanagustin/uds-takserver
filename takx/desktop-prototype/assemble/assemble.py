#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Assemble a runnable TAKX 5.7 tree for the desktop prototype.

The output mirrors the official ``deployment/bin`` directory that the Gradle
``createBin`` task fills (``gradle/binCreator.gradle``): ``boot/``, ``load/``,
``lib/``, ``modules/``, the tracked ``deployment/bin`` files and
``resources/tak-assets``. Nothing is compiled here and no file is invented:
every byte is copied from a recipe output, from the official TAKX tree or from
the public atak-civ tree, and ``manifest.json`` records the source and SHA-256
of each file.

Substitutes, stated once here and again in the manifest:

* Kernel: public 8.30.5 plus project patches (not the private 8.57.11).
* CommonCommo: public 1.17.2 patched (not the private 1.20.0).
* WorldWind: NASA v2.2.1 interop build (not the private 2.2.1-TAKX-4).
* UI libraries: the takx/ui-java source rebuilds.
* tak-assets: only files that exist in public atak-civ. The private
  ``png/`` icon directory and ``border-shapefiles`` are absent.

Left out for their licence (``LICENCE_EXCLUDED``, ``manifest.json``
``licence_exclusions``): the bytedeco FFmpeg native JAR, whose binaries state
"nonfree and unredistributable" (video needs a user-supplied add-on, see
``launch.sh``); the CDDL-only wizard reference JAR, which no class of the
client refers to; and joost (MPL-1.1 only, a ``runtimeOnly`` declaration no
class refers to). The Kernel JAR in the tree is a derived copy
(``derive_jar.py``): font atlases without a licence removed, RUNPATH removed
from two native libraries; the manifest records both digests.

JBoss Modules descriptors are written by hand because the official generator
(the smartics plugin behind a private plugin repository configuration) cannot
run here. The module names that ``ApplicationModuleFinder`` resolves from the
boot loader exist with those exact names; every other third-party JAR lives in
one module, ``prototype.libs``, which is coarser than the official
one-module-per-artifact graph.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from xml.sax.saxutils import quoteattr

KERNEL_LABEL = 'Kernel 8.30.5 + project patches (texture, render-context), unqualified'
COMMO_LABEL = 'CommonCommo 1.17.2 patched'
WORLDWIND_LABEL = 'NASA WorldWind Java v2.2.1 + interop profile'

# Module names ApplicationModuleFinder asks the boot module loader for.
LOG4J = 'log4j'
SLF4J = 'org.slf4j.slf4j-api'
ASPECTJ = 'org.aspectj'
CDI = 'jakarta.enterprise.jakarta.enterprise.cdi-api'
COMMO = 'jcommoncommo'
KERNEL = 'gov.tak.kernel'
LIBS = 'prototype.libs'
ALL_LIBS = 'all-libs'

GROUP_MODULE = {
    'org.apache.logging.log4j': LOG4J,
    'org.slf4j': SLF4J,
    'org.aspectj': ASPECTJ,
    'jakarta.enterprise': CDI,
}

# Official exclusions (binCreator.gradle dependencyExcludes and the
# jaxb-runtime exclusion of the dependency scripts) that occur in the resolved
# runtime closure.
EXCLUDED = {
    'org.glassfish.jaxb:jaxb-runtime': 'excluded by the official dependency scripts',
    'org.jboss.logging:jboss-logging-annotations': 'excluded by binCreator.gradle dependencyExcludes',
    'org.jboss.modules:jboss-modules': 'placed in boot/, excluded from modules by binCreator.gradle',
}
FOREIGN_NATIVES = re.compile(r'natives-(?!linux-amd64$)')

# Kernel dependency JARs whose classes the TAKX runtime closure already
# provides under another artifact name.
KERNEL_SAME_CLASSES = {
    'eventbus': 'org.greenrobot:eventbus-java',
    'annotations': 'org.jetbrains:annotations',
}

# Artifacts of the resolved runtime closure that are not shipped because of
# their licence. Key: 'group:artifact' and whether the rule is for the JARs
# with a classifier (native JARs) or for all of them. image/licence_gate.py
# checks the result independently, by content.
LICENCE_EXCLUDED = {
    ('org.bytedeco:ffmpeg', 'classifier'): {
        'licence': 'the binaries state "nonfree and unredistributable" (built with --enable-nonfree)',
        'effect': 'no FFmpeg natives: MediaPlayerManager.startService fails with UnsatisfiedLinkError (no jniavutil) '
                  'when a project opens, the error is logged once and the other project services start; video '
                  'playback, recording and media length analysis do not work unless the user supplies natives '
                  '(launch.sh, TAKX_ADDON_LIBRARY_PATH)',
        'evidence': 'boot of the tree without the JAR; the Java wrapper JARs (ffmpeg, javacpp) stay'},
    ('net.sf.joost:joost', 'all'): {
        'licence': 'MPL-1.1 only (POM)',
        'effect': 'none seen: a runtimeOnly declaration of gradle/platformDependencies.gradle',
        'evidence': 'no class, service file or XML of any other JAR in the tree names net.sf.joost'},
}
# Recipe outputs that are not shipped because of their licence.
LICENCE_EXCLUDED_UI = {
    'ui-wizard-reference': {
        'artifact': 'uds-takserver-ui-wizard-reference.jar',
        'licence': 'CDDL-1.0 only (source notices of the JAR)',
        'effect': 'none seen: gradle/clientDependencies.gradle declares org.netbeans:wizard, but no class of the '
                  'client or of any other JAR refers to org.netbeans.api.wizard, org.netbeans.spi.wizard or '
                  'org.netbeans.modules.wizard',
        'evidence': 'constant pools and service files of every JAR in the tree'},
}
# Data directories of two native providers. The libraries carry the build
# paths of these directories; a tree that runs elsewhere needs the files
# and GDAL_DATA / PROJ_LIB (launch.sh sets both).
PROVIDER_DATA = (
    ('share/gdal', ('P', 'scene', 'gdal-install', 'share', 'gdal'),
     'GDAL 2.4.4 data directory (share/gdal of the GDAL install built by lane 3, P/scene/gdal-install)'),
    ('share/proj', ('P', 'proj', 'install', 'share', 'proj'),
     'PROJ 4.9.1 data directory (share/proj of the PROJ install built by lane 1, P/proj/install)'),
)

MODULE_DEPENDENCIES = {
    LOG4J: [('jdk', {}), (SLF4J, {})],
    SLF4J: [('jdk', {}), (LOG4J, {'services': 'export'})],
    ASPECTJ: [('jdk', {})],
    CDI: [('jdk', {}), (LIBS, {})],
    COMMO: [('jdk', {})],
    KERNEL: [('jdk', {}), (SLF4J, {}), (LOG4J, {}), (LIBS, {})],
    LIBS: [('jdk', {'services': 'import'}), (LOG4J, {'services': 'import'}), (SLF4J, {}),
           (ASPECTJ, {}), (CDI, {'export': 'true'}), (COMMO, {}), (KERNEL, {}),
           ('org.jboss.modules', {})],
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


class Assembly:
    def __init__(self, root, out):
        self.root = root
        self.out = out
        self.files = []
        self.notes = []

    def put(self, source, relative, origin, mode=None):
        target = os.path.join(self.out, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise SystemExit('assemble: duplicate output %s' % relative)
        shutil.copyfile(source, target)
        if mode is not None:
            os.chmod(target, mode)
        self.files.append({'path': relative, 'sha256': sha256(target),
                           'bytes': os.path.getsize(target), 'origin': origin,
                           'source': source})
        return target

    def write(self, relative, text, origin):
        target = os.path.join(self.out, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.lexists(target):
            raise SystemExit('assemble: duplicate output %s' % relative)
        with open(target, 'w', encoding='utf-8') as stream:
            stream.write(text)
        self.files.append({'path': relative, 'sha256': sha256(target),
                           'bytes': os.path.getsize(target), 'origin': origin,
                           'source': 'generated by assemble.py'})

    def replace(self, relative, source, new_relative, origin, extra=None):
        """Take back the file put at ``relative`` and put ``source`` at ``new_relative`` instead."""
        for index, item in enumerate(self.files):
            if item['path'] == relative:
                del self.files[index]
                break
        else:
            raise SystemExit('assemble: nothing was put at %s' % relative)
        os.remove(os.path.join(self.out, relative))
        self.put(source, new_relative, origin)
        self.files[-1].update(extra or {})

    def tree(self, source_dir, relative_dir, origin, skip=()):
        count = 0
        for base, dirs, names in os.walk(source_dir):
            dirs.sort()
            for name in sorted(names):
                if name in skip:
                    continue
                path = os.path.join(base, name)
                rel = os.path.relpath(path, source_dir)
                self.put(path, os.path.join(relative_dir, rel), origin)
                count += 1
        return count


def need(path):
    if not os.path.exists(path):
        raise SystemExit('assemble: missing input %s' % path)
    return path


def status_ok(root, stage):
    path = os.path.join(root, 'status', stage + '.json')
    need(path)
    with open(path) as stream:
        if json.load(stream).get('status') != 'ok':
            raise SystemExit('assemble: stage %s is not ok' % stage)


def module_xml(name, jars, dependencies, comment):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '',
             '<!-- %s -->' % comment,
             '<module xmlns="urn:jboss:module:1.9" name=%s>' % quoteattr(name)]
    if jars:
        lines.append('    <resources>')
        lines.extend('        <resource-root path=%s/>' % quoteattr(jar) for jar in jars)
        lines.append('    </resources>')
    lines.append('    <dependencies>')
    for dependency, attributes in dependencies:
        attributes = dict(attributes)
        paths = attributes.pop('paths', ())
        extra = ''.join(' %s=%s' % (key, quoteattr(value)) for key, value in sorted(attributes.items()))
        if not paths:
            lines.append('        <module name=%s%s/>' % (quoteattr(dependency), extra))
            continue
        lines.append('        <module name=%s%s>' % (quoteattr(dependency), extra))
        for element in ('imports', 'exports'):
            lines.append('            <%s>' % element)
            lines.extend('                <include path=%s/>' % quoteattr(path) for path in paths)
            lines.append('            </%s>' % element)
        lines.append('        </module>')
    lines.append('    </dependencies>')
    lines.append('</module>')
    return '\n'.join(lines) + '\n'


def module_dir(name):
    return os.path.join('modules', *name.split('.'), 'main')


def maven_file_name(entry):
    # manifest files are "jars/<group>--<artifact>-<version>[-<classifier>].jar";
    # the Maven name is kept so that JogAmp finds "<jar>-natives-<os>.jar"
    # next to its class JAR.
    return os.path.basename(entry['file']).split('--', 1)[1]


def artifact_of(file_name):
    match = re.match(r'(.+?)-\d', file_name)
    return match.group(1) if match else file_name[:-4]


def lfs_pointer(path):
    with open(path, 'rb') as stream:
        head = stream.read(200).decode('ascii', 'replace')
    oid = re.search(r'oid sha256:([0-9a-f]{64})', head)
    size = re.search(r'size (\d+)', head)
    if not head.startswith('version https://git-lfs') or not oid or not size:
        return None
    return oid.group(1), int(size.group(1))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--overlay', help='Kernel overlay directory (default ROOT/P/kernel-overlay)')
    parser.add_argument('--overlay-status', default='kernel-overlay',
                        help='name of the status file of the overlay step (default kernel-overlay)')
    parser.add_argument('--kernel-derived', required=True,
                        help='directory with the derived Kernel JAR and derive-receipt.json (derive_jar.py)')
    args = parser.parse_args()
    root, out = args.root, args.output
    started = time.time()
    if os.path.exists(out):
        raise SystemExit('assemble: output %s exists' % out)
    for stage in ('takx57', 'takx57-runtime-jars', 'kernel-composed', args.overlay_status, 'compat-system-clock',
                  'commoncommo', 'worldwind-interop', 'ui-original'):
        status_ok(root, stage)

    takx = need(os.path.join(root, 'src', 'takx57'))
    atak = need(os.path.join(root, 'src', 'atak-civ'))
    atak_lfs = need(os.path.join(root, 'src', 'atak-civ-lfs'))
    package = need(os.path.join(root, 'P', 'takx57', 'package'))
    runtime = need(os.path.join(root, 'P', 'takx57-runtime-jars'))
    kernel = need(os.path.join(root, 'P', 'kernel-composed', 'package'))
    commo = need(os.path.join(root, 'P', 'commoncommo', 'install'))
    assembly = Assembly(root, out)
    os.makedirs(out)

    # --- tracked deployment/bin files (conf, gazetteer, resources, log4j, vmoptions)
    tracked = assembly.tree(os.path.join(takx, 'deployment', 'bin'), '',
                            'official TAKX 5.7 deployment/bin', skip=('.gitignore', 'read_vmoptions.bat'))
    os.makedirs(os.path.join(out, 'plugins'), exist_ok=True)

    # --- load/ and boot/
    version = None
    for name in sorted(os.listdir(os.path.join(package, 'load'))):
        assembly.put(os.path.join(package, 'load', name), os.path.join('load', name),
                     'takx/desktop-prototype client stage (official TAKX 5.7 sources)')
        match = re.match(r'bootstrap-(.+)\.jar$', name)
        if match:
            version = match.group(1)
    if version is None:
        raise SystemExit('assemble: no bootstrap JAR in %s/load' % package)
    for name in sorted(os.listdir(os.path.join(package, 'boot'))):
        assembly.put(os.path.join(package, 'boot', name), os.path.join('boot', name),
                     'takx/desktop-prototype client stage (official TAKX 5.7 sources)')

    with open(os.path.join(runtime, 'manifest.json')) as stream:
        closure = json.load(stream)
    members = {LOG4J: [], SLF4J: [], ASPECTJ: [], CDI: [], COMMO: [], KERNEL: [], LIBS: []}
    first = {}  # resource roots that must precede the sorted members of a module
    left_out = []
    licence_exclusions = []
    runtime_artifacts = {}
    for entry in closure['jars']:
        group, artifact, coordinate_version = entry['coordinates'].split(':')[:3]
        source = need(os.path.join(runtime, entry['file']))
        if sha256(source) != entry['sha256']:
            raise SystemExit('assemble: %s does not match its manifest digest' % source)
        name = maven_file_name(entry)
        key = '%s:%s' % (group, artifact)
        origin = 'public Maven artifact %s (%s)' % (entry['coordinates'], entry['repository'])
        if key == 'org.jboss.modules:jboss-modules':
            assembly.put(source, os.path.join('boot', name), origin)
            continue
        if key in EXCLUDED:
            left_out.append({'coordinates': entry['coordinates'], 'reason': EXCLUDED[key]})
            continue
        has_classifier = len(entry['coordinates'].split(':')) > 3
        rule = LICENCE_EXCLUDED.get((key, 'all')) or (LICENCE_EXCLUDED.get((key, 'classifier')) if has_classifier else None)
        if rule:
            licence_exclusions.append(dict(rule, coordinates=entry['coordinates'], file=name, sha256=entry['sha256'],
                                           bytes=entry['bytes']))
            continue
        classifier = name[len(artifact) + len(coordinate_version) + 2:-4]
        if FOREIGN_NATIVES.match(classifier):
            left_out.append({'coordinates': entry['coordinates'],
                             'reason': 'natives of another platform; Linux amd64 prototype only'})
            continue
        module = GROUP_MODULE.get(group, LIBS)
        if name in members[module]:
            # Two groups publish the same file name (the two jaxb-core JARs):
            # the later one keeps its group in the name.
            name = '%s--%s' % (group, name)
        assembly.put(source, os.path.join(module_dir(module), name), origin)
        members[module].append(name)
        runtime_artifacts.setdefault(artifact, coordinate_version)

    # --- UI and WorldWind substitutes
    for directory, rule in sorted(LICENCE_EXCLUDED_UI.items()):
        path = os.path.join(root, 'P', directory, rule['artifact'])
        licence_exclusions.append(dict(rule, recipe_output='P/%s/%s' % (directory, rule['artifact']),
                                       sha256=sha256(path) if os.path.isfile(path) else None))
    for directory, stage in (('ui-original', 'takx/ui-java'),):
        base = os.path.join(root, 'P', directory)
        for name in sorted(os.listdir(base)):
            if name.endswith('.jar') and not name.endswith('-sources.jar'):
                assembly.put(os.path.join(base, name), os.path.join(module_dir(LIBS), name),
                             'public source rebuild by %s (substitute for a private artifact)' % stage)
                members[LIBS].append(name)
    assembly.put(need(os.path.join(root, 'P', 'worldwind-interop', 'uds-takserver-worldwind-java-interop.jar')),
                 os.path.join('lib', 'worldwind-2.2.1-nasa-public-interop.jar'),
                 '%s (takx/worldwind-java --interop; not the private 2.2.1-TAKX-4)' % WORLDWIND_LABEL)

    # --- Kernel and its dependency JARs
    kernel_jar = need(os.path.join(kernel, 'kernel-8.30.5-linux-amd64-public-cpu.jar'))
    assembly.put(kernel_jar, os.path.join(module_dir(KERNEL), os.path.basename(kernel_jar)),
                 '%s (takx/desktop-prototype kernel stage; not the private 8.57.11)' % KERNEL_LABEL)
    members[KERNEL].append(os.path.basename(kernel_jar))
    # The tree does not ship that JAR as built: derive_jar.py made a copy
    # without the font atlases that have no licence and without the RUNPATH
    # of two native libraries. It gets its own name; the Kernel receipt and
    # controls describe the JAR named above, and the manifest says so.
    with open(os.path.join(args.kernel_derived, 'derive-receipt.json')) as stream:
        derivation = json.load(stream)
    if derivation['input']['sha256'] != sha256(kernel_jar) or \
            derivation['output']['sha256'] != sha256(derivation['output']['path']):
        raise SystemExit('assemble: the derived Kernel JAR does not match its receipt or the current Kernel JAR')
    built_name = os.path.basename(kernel_jar)
    derived_name = built_name[:-4] + '-tree.jar'
    members[KERNEL][members[KERNEL].index(built_name)] = derived_name
    assembly.replace(os.path.join(module_dir(KERNEL), built_name), derivation['output']['path'],
                     os.path.join(module_dir(KERNEL), derived_name),
                     '%s (takx/desktop-prototype kernel stage; not the private 8.57.11); derived copy of %s '
                     '(sha256 %s) by assemble/derive_jar.py: %d font atlas files removed, %d entries changed'
                     % (KERNEL_LABEL, built_name, derivation['input']['sha256'], len(derivation['removed']),
                        len(derivation['changed'])),
                     {'derived_from_sha256': derivation['input']['sha256']})
    # The overlay JAR (kernel_overlay.py) holds Kernel classes rebuilt with the
    # recorded prototype patches, and from recipe sources newer than the
    # composed Kernel; it precedes the Kernel JAR in the module.
    overlay_dir = need(args.overlay or os.path.join(root, 'P', 'kernel-overlay'))
    with open(os.path.join(overlay_dir, 'receipt.json')) as stream:
        overlay = json.load(stream)
    if overlay['kernel_classes_jar']['sha256'] != sha256(overlay['kernel_classes_jar']['path']) or \
            sha256(overlay['jar']['path']) != overlay['jar']['sha256']:
        raise SystemExit('assemble: the Kernel overlay does not match its receipt or the current Kernel classes')
    overlay_name = os.path.basename(overlay['jar']['path'])
    refresh = overlay.get('recipe_refresh')
    assembly.put(overlay['jar']['path'], os.path.join(module_dir(KERNEL), overlay_name),
                 'desktop-prototype Kernel Java overlay: %d Kernel classes rebuilt with %s%s'
                 % (len(overlay['classes']), ', '.join(item['patch'] for item in overlay['patches']),
                    '; %d source(s) refreshed from %s (patch sha256 %s)'
                    % (len(refresh['sources']), refresh['recipe'], refresh['context_patch_sha256']) if refresh else ''))
    first[KERNEL] = [overlay_name]
    superseded = []
    for name in sorted(os.listdir(os.path.join(kernel, 'third-party'))):
        artifact = artifact_of(name)
        if artifact in runtime_artifacts:
            superseded.append({'kernel_dependency': name,
                               'runtime_closure_version': runtime_artifacts[artifact]})
            continue
        if artifact in KERNEL_SAME_CLASSES:
            superseded.append({'kernel_dependency': name,
                               'runtime_closure_artifact': KERNEL_SAME_CLASSES[artifact]})
            continue
        # The JNI-bearing GDAL and jassimp classes must share the class loader
        # that maps their native libraries (the Kernel loader), so the Kernel
        # dependency JARs live in the Kernel module.
        assembly.put(os.path.join(kernel, 'third-party', name), os.path.join(module_dir(KERNEL), name),
                     'Kernel 8.30.5 dependency JAR frozen by takx/kernel-java')
        members[KERNEL].append(name)

    # The stock EngineLibrary loader extracts the natives of the Kernel JAR
    # below java.io.tmpdir; the launcher puts that directory on
    # java.library.path so that System.loadLibrary calls of the GDAL binding
    # resolve to the very files the loader already mapped.
    with open(os.path.join(root, 'status', 'kernel-composed.json')) as stream:
        kernel_status = json.load(stream)
    if sha256(kernel_jar) != kernel_status['jar']['sha256']:
        raise SystemExit('assemble: the Kernel JAR is not the one status/kernel-composed.json describes')
    prefix = '${java.io.tmpdir}/'
    loader_path = kernel_status['stock_loader_java_library_path']
    built_md5 = hashlib.new('md5', usedforsecurity=False)
    with open(kernel_jar, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            built_md5.update(block)
    if not loader_path.startswith(prefix) or '/%s/' % built_md5.hexdigest() not in loader_path:
        raise SystemExit('assemble: unexpected stock loader path %s' % loader_path)
    # The stock loader names its directory after the MD5 of the JAR it is
    # loaded from, here the derived copy.
    loader_path = loader_path.replace('/%s/' % built_md5.hexdigest(), '/%s/' % derivation['output']['md5'])
    # The desktop android.content.res classes of the Kernel build (takx/compat)
    # read their resource catalog from META-INF of the Kernel JAR; all-libs
    # passes that path through, because a JBoss Modules dependency otherwise
    # exports META-INF/services only.
    assembly.write('prototype-launch.env',
                   '# Read by takx/desktop-prototype/assemble/launch.sh.\n'
                   'KERNEL_STOCK_LOADER_SUBDIR=%s\n' % loader_path[len(prefix):],
                   'launcher fact: status/kernel-composed.json stock_loader_java_library_path with the MD5 of '
                   'the derived Kernel JAR, relative to java.io.tmpdir')

    # --- data directories of GDAL and PROJ
    provider_data = {}
    for relative, parts, origin in PROVIDER_DATA:
        provider_data[relative] = assembly.tree(need(os.path.join(root, *parts)), relative, origin)

    # --- CommonCommo
    assembly.put(need(os.path.join(commo, 'java', 'jcommoncommo.jar')),
                 os.path.join(module_dir(COMMO), 'jcommoncommo-1.17.2-public-patched.jar'),
                 '%s (takx/native-commoncommo; not the private 1.20.0)' % COMMO_LABEL)
    members[COMMO].append('jcommoncommo-1.17.2-public-patched.jar')
    assembly.put(need(os.path.join(commo, 'lib', 'libcommoncommojni.so')),
                 os.path.join('native', 'linux64', 'libcommoncommojni.so'),
                 '%s JNI library (takx/native-commoncommo)' % COMMO_LABEL, mode=0o755)

    # --- desktop SystemClock JNI library of the Kernel build's android.os.SystemClock
    clock = need(os.path.join(root, 'P', 'compat-system-clock', 'libuds_desktop_clock.so'))
    assembly.put(clock, os.path.join('native', 'linux64', 'libuds_desktop_clock.so'),
                 'takx/compat/build-system-clock.py (project JNI source for android.os.SystemClock of the Kernel build)',
                 mode=0o755)

    # --- modules
    comments = {
        LOG4J: 'Prototype descriptor; official name from bin-creator log4j.xml.',
        SLF4J: 'Prototype descriptor; official name from bin-creator org.slf4j.slf4j-api.xml.',
        ASPECTJ: 'Prototype descriptor; official name from bin-creator org.aspectj.xml.',
        CDI: 'Prototype descriptor; name required by ApplicationModuleFinder.',
        COMMO: 'Prototype descriptor; holds %s, not the private 1.20.0.' % COMMO_LABEL,
        KERNEL: 'Prototype descriptor; holds %s, not the private 8.57.11.' % KERNEL_LABEL,
        LIBS: 'Prototype descriptor; every other third-party JAR in one module (the official build generates one module per artifact).',
    }
    for module in (LOG4J, SLF4J, ASPECTJ, CDI, COMMO, KERNEL, LIBS):
        if not members[module]:
            raise SystemExit('assemble: module %s has no JAR' % module)
        assembly.write(os.path.join(module_dir(module), 'module.xml'),
                       module_xml(module, first.get(module, []) + sorted(members[module]),
                                  MODULE_DEPENDENCIES[module], comments[module]),
                       'hand-written JBoss Modules descriptor')
    assembly.write(os.path.join(module_dir(ALL_LIBS), 'module.xml'),
                   module_xml(ALL_LIBS, [], [(module, dict({'export': 'true', 'services': 'export'},
                                                            **({'paths': ('META-INF',)} if module == KERNEL else {})))
                                             for module in (LOG4J, SLF4J, ASPECTJ, CDI, COMMO, KERNEL, LIBS)],
                              'Prototype descriptor; same shape as the buildAllLibsModule output of binCreator.gradle.'),
                   'hand-written JBoss Modules descriptor')
    custom = os.path.join(takx, 'bin-creator', 'src', 'main', 'resources', 'modules')
    assembly.put(os.path.join(custom, 'jdk', 'main', 'module.xml'), os.path.join('modules', 'jdk', 'main', 'module.xml'),
                 'official TAKX 5.7 bin-creator custom module')
    with open(os.path.join(custom, 'takx-bootstrap', 'main', 'module.xml'), encoding='utf-8') as stream:
        bootstrap = stream.read()
    if '@platformVersion@' not in bootstrap:
        raise SystemExit('assemble: the takx-bootstrap module has no @platformVersion@ token')
    assembly.write(os.path.join('modules', 'takx-bootstrap', 'main', 'module.xml'),
                   bootstrap.replace('@platformVersion@', version),
                   'official TAKX 5.7 bin-creator custom module with @platformVersion@ = %s (the copyCustomModules filter)' % version)

    # --- public tak-assets subset
    assets = os.path.join(atak, 'atak', 'ATAK', 'app', 'src', 'main', 'assets')
    origin = 'public atak-civ 9f6893dd atak/ATAK/app/src/main/assets'
    assembly.put(need(os.path.join(assets, 'callsigns.txt')), 'resources/tak-assets/txt/callsigns.txt', origin)
    assembly.put(need(os.path.join(assets, 'dbs', 'iconcache.sqlite')), 'resources/tak-assets/dbs/iconcache.sqlite', origin)
    icons = assembly.tree(need(os.path.join(assets, 'icons')), 'resources/tak-assets/icons', origin)
    iconsets = 0
    pointers = os.path.join(assets, 'iconsets')
    for name in sorted(os.listdir(pointers)):
        if not name.endswith('.zip'):
            continue
        pointer = lfs_pointer(os.path.join(pointers, name))
        if pointer is None:
            raise SystemExit('assemble: %s is not a Git LFS pointer' % name)
        media = need(os.path.join(atak_lfs, 'atak', 'ATAK', 'app', 'src', 'main', 'assets', 'iconsets', name))
        if sha256(media) != pointer[0] or os.path.getsize(media) != pointer[1]:
            raise SystemExit('assemble: %s does not match its Git LFS pointer' % media)
        if not zipfile.is_zipfile(media):
            raise SystemExit('assemble: %s is not a ZIP archive' % media)
        assembly.put(media, os.path.join('resources/tak-assets/iconsets', name),
                     origin + ' (Git LFS object, verified against the pointer)')
        iconsets += 1

    manifest = {
        'schema': 1,
        'stage': 'assemble',
        'layout': 'mirror of official TAKX 5.7 deployment/bin (gradle/binCreator.gradle createBin)',
        'takx': {'source_revision': '0b59a9bb2d0e90dc35c9edfff1c2056daab2b713', 'jar_version': version},
        'components': {
            'kernel': KERNEL_LABEL + '; substitute for the private 8.57.11',
            'commoncommo': COMMO_LABEL + '; substitute for the private 1.20.0',
            'worldwind': WORLDWIND_LABEL + '; substitute for the private 2.2.1-TAKX-4',
            'ui_libraries': 'takx/ui-java source rebuilds; substitutes for private artifacts',
            'tak_assets': 'public atak-civ files only: txt/callsigns.txt, dbs/iconcache.sqlite, icons/, iconsets/; the private png/ directory is absent',
            'border_shapefiles': 'absent',
            'postgresql': 'absent; the default database is hsqldb',
        },
        'modules': {name: first.get(name, []) + sorted(jars) for name, jars in members.items()},
        'kernel_jar_derivation': {'tool': 'takx/desktop-prototype/assemble/derive_jar.py',
                                  'built': derivation['input'], 'shipped': derivation['output'],
                                  'shipped_as': os.path.join(module_dir(KERNEL), derived_name),
                                  'removed': derivation['removed'], 'changed': derivation['changed'],
                                  'unchanged_entries': derivation['unchanged_entries'],
                                  'unchanged_entries_sha256': derivation['unchanged_entries_sha256'],
                                  'note': 'the Kernel receipt and the nine Kernel controls describe the built JAR; '
                                          'no control was rerun with the shipped copy'},
        'licence_exclusions': licence_exclusions,
        'provider_data_files': provider_data,
        'kernel_java_overlay': {'patches': overlay['patches'], 'recipe_refresh': refresh,
                                'classes': sorted(overlay['classes']),
                                'jar_sha256': overlay['jar']['sha256'],
                                'kernel_controls_rerun_with_overlay': False},
        'module_graph_note': 'hand-written; coarser than the official generated one-module-per-artifact graph',
        'left_out': left_out,
        'kernel_dependencies_superseded_by_runtime_closure': superseded,
        'counts': {'files': len(assembly.files), 'tracked_deployment_files': tracked,
                   'tak_asset_icons': icons, 'tak_asset_iconsets': iconsets,
                   'bytes': sum(item['bytes'] for item in assembly.files)},
        'files': sorted(assembly.files, key=lambda item: item['path']),
        'seconds': round(time.time() - started, 1),
    }
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf-8') as stream:
        json.dump(manifest, stream, indent=1, sort_keys=True)
        stream.write('\n')
    print(json.dumps(manifest['counts']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
