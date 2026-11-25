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
    args = parser.parse_args()
    root, out = args.root, args.output
    started = time.time()
    if os.path.exists(out):
        raise SystemExit('assemble: output %s exists' % out)
    for stage in ('takx57', 'takx57-runtime-jars', 'kernel-composed', 'commoncommo',
                  'worldwind-interop', 'ui-original', 'ui-wizard-reference'):
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
    left_out = []
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
    for directory, stage in (('ui-original', 'takx/ui-java'), ('ui-wizard-reference', 'takx/ui-java wizard reference')):
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
    if not loader_path.startswith(prefix):
        raise SystemExit('assemble: unexpected stock loader path %s' % loader_path)
    # The desktop android.content.res classes of the Kernel build (takx/compat)
    # serve assets only from a JAR named on java.class.path, and read their
    # resource catalog from META-INF of the Kernel JAR. Under JBoss Modules
    # neither holds by default, so the launcher appends the Kernel JAR to the
    # class path and all-libs passes the Kernel META-INF path through.
    kernel_relative = os.path.join(module_dir(KERNEL), os.path.basename(kernel_jar))
    assembly.write('prototype-launch.env',
                   '# Read by takx/desktop-prototype/assemble/launch.sh.\n'
                   'KERNEL_STOCK_LOADER_SUBDIR=%s\n'
                   'KERNEL_ASSET_ROOT_JAR=%s\n' % (loader_path[len(prefix):], kernel_relative),
                   'launcher facts: status/kernel-composed.json stock_loader_java_library_path '
                   '(relative to java.io.tmpdir) and the Kernel JAR of the gov.tak.kernel module')

    # --- CommonCommo
    assembly.put(need(os.path.join(commo, 'java', 'jcommoncommo.jar')),
                 os.path.join(module_dir(COMMO), 'jcommoncommo-1.17.2-public-patched.jar'),
                 '%s (takx/native-commoncommo; not the private 1.20.0)' % COMMO_LABEL)
    members[COMMO].append('jcommoncommo-1.17.2-public-patched.jar')
    assembly.put(need(os.path.join(commo, 'lib', 'libcommoncommojni.so')),
                 os.path.join('native', 'linux64', 'libcommoncommojni.so'),
                 '%s JNI library (takx/native-commoncommo)' % COMMO_LABEL, mode=0o755)

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
                       module_xml(module, sorted(members[module]), MODULE_DEPENDENCIES[module], comments[module]),
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
        'modules': {name: sorted(jars) for name, jars in members.items()},
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
