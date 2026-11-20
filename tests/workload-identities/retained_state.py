# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Private invariants and exact retained-application cleanup, never Core cleanup."""
import base64
import copy
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
import ssl
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
import yaml

APP_RELEASES={'takserver':{'takserver','uds-takserver-config'},'cloudtak':{'uds-cloudtak'}}
GENERATED_SECRETS={'takserver-ca-key','takserver-certs','tak-health-client'}
SOURCE_CLEANUP={(ns,'Secret',name) for ns,name in [('takserver',name) for name in GENERATED_SECRETS|{'takserver-keystore','takclient-enrollment','takserver-crl'}]+[('cloudtak','cloudtak-tak-admin')]}|{('takserver','ConfigMap','cert-migration'),('takserver','ConfigMap','takserver-startup'),('takserver','ConfigMap','ignite-extensions')}

def cleanup_allowed(rec):
    if (rec.get('namespace'),rec.get('kind'),rec.get('name'))==('takserver','Secret','takserver-crl'):
        return isinstance(rec.get('emptyCrlDataSHA256'),str) and bool(re.fullmatch('[a-f0-9]{64}',rec['emptyCrlDataSHA256']))
    if (rec.get('namespace'),rec.get('kind'),rec.get('name'))==('takserver','ConfigMap','ignite-extensions'):
        return all(isinstance(rec.get(key),str) and bool(re.fullmatch('[a-f0-9]{64}',rec[key])) for key in ('imageBoundJarSHA256','imageSourceSHA256'))
    return rec['namespace'] in APP_RELEASES and (rec['kind']=='PersistentVolumeClaim' and 'parent' in rec or (rec['namespace'],rec['kind'],rec['name']) in SOURCE_CLEANUP)

def require(value,message):
    if not value:raise ValueError(message)

def sha(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

SOURCE_FILES=('pepr/capabilities/workload.ts','pepr/capabilities/cloudtak-admin.ts','pepr/capabilities/takclient-credentials.ts','pepr/capabilities/takclient.ts','pepr/capabilities/takclient-pki.ts','pepr/capabilities/takclient-capability.ts','.github/container-and-chart/docker/Dockerfile','.github/container-and-chart/docker/publish-cert-configmap.sh')

def source_files():
    return {name:hashlib.sha256((Path(__file__).resolve().parents[2]/name).read_bytes()).hexdigest() for name in SOURCE_FILES}

# This is the complete config-chart hook inventory, not an allowlist of fields
# to ignore. Every object field contributes to its canonical SHA-256.
HOOKS={
    ('v1','Secret','takserver-keystore'):'keystore',
    ('networking.k8s.io/v1','NetworkPolicy','takserver-cert-init-kube-api'):'cert-network',
    ('v1','ServiceAccount','takserver-cert-init'):'cert-account',
    ('rbac.authorization.k8s.io/v1','Role','takserver-cert-init'):'cert-role',
    ('rbac.authorization.k8s.io/v1','RoleBinding','takserver-cert-init'):'cert-binding',
    ('batch/v1','Job','takserver-cert-init'):'cert-job',
}
FIELD_KEYS={
    'metadata':{'metadata'}, 'credential-data':{'data','stringData','type','immutable'},
    'job-spec':{'spec'}, 'rbac':{'rules','roleRef','subjects'},
    'network-policy':{'spec'},
    'identity-spec':{'automountServiceAccountToken','secrets','imagePullSecrets'},
}
HOOK_CLASSES={'keystore':('metadata','credential-data','object-fields'),
              'cert-network':('metadata','network-policy','object-fields'),
              'cert-account':('metadata','identity-spec','object-fields'),
              'cert-role':('metadata','rbac','object-fields'),
              'cert-binding':('metadata','rbac','object-fields'),
              'cert-job':('metadata','job-spec','object-fields')}
ORIGIN_HASHES=('storedHooksSHA256','sourceGeneratorJobSHA256','keystoreHookSHA256')

def hook_inventory(objects):
    require(isinstance(objects,list) and len(objects)==len(HOOKS),'stored hook inventory differs')
    result={}
    for obj in objects:
        require(isinstance(obj,dict) and isinstance(obj.get('metadata'),dict),'stored hook inventory differs')
        key=(obj.get('apiVersion'),obj.get('kind'),obj['metadata'].get('name'))
        require(key in HOOKS and HOOKS[key] not in result,'stored hook inventory differs')
        code=HOOKS[key];classes=HOOK_CLASSES[code];used=set();fields={}
        for category in classes[:-1]:
            names=FIELD_KEYS[category];used|=names
            fields[category]=sha({name:obj[name] for name in names if name in obj})
        fields['object-fields']=sha({name:value for name,value in obj.items() if name not in used})
        result[code]={'sha256':sha(obj),'fieldSHA256':fields}
    require(set(result)==set(HOOK_CLASSES),'stored hook inventory differs')
    return {'schema':1,'objects':result}

def valid_inventory(value):
    if not isinstance(value,dict) or set(value)!={'schema','objects'} or type(value['schema']) is not int or value['schema']!=1:return False
    objects=value['objects']
    if not isinstance(objects,dict) or set(objects)!=set(HOOK_CLASSES):return False
    for code,entry in objects.items():
        if not isinstance(entry,dict) or set(entry)!={'sha256','fieldSHA256'}:return False
        fields=entry['fieldSHA256']
        if not isinstance(fields,dict) or set(fields)!=set(HOOK_CLASSES[code]):return False
        if not all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{64}',v) for v in [entry['sha256'],*fields.values()]):return False
    return True

def valid_origin(origin):
    return (isinstance(origin,dict) and set(origin)=={'sourceFilesSHA256',*ORIGIN_HASHES,'hookInventory'}
            and isinstance(origin['sourceFilesSHA256'],dict) and set(origin['sourceFilesSHA256'])==set(SOURCE_FILES)
            and all(isinstance(v,str) and re.fullmatch(r'[a-f0-9]{64}',v) for v in [*origin['sourceFilesSHA256'].values(),*[origin[k] for k in ORIGIN_HASHES]])
            and valid_inventory(origin['hookInventory'])
            and origin['keystoreHookSHA256']==origin['hookInventory']['objects']['keystore']['sha256']
            and origin['sourceGeneratorJobSHA256']==origin['hookInventory']['objects']['cert-job']['sha256'])

def require_origin(rec):
    origin=rec.get('origin',{})
    require(valid_origin(origin) and origin['sourceFilesSHA256']==source_files(),'retained cleanup has no unchanged bound source origin')

def hook_diagnostic(baseline,observed):
    if not valid_origin(baseline) or not valid_origin(observed):return {'hook':'inventory','category':'inventory','fieldClass':'inventory'}
    if baseline['sourceFilesSHA256']!=observed['sourceFilesSHA256']:return {'hook':'source-code','category':'source-code','fieldClass':'source-code'}
    for code,classes in HOOK_CLASSES.items():
        old=baseline['hookInventory']['objects'][code];new=observed['hookInventory']['objects'][code]
        if old['sha256']==new['sha256']:continue
        for category in classes:
            if old['fieldSHA256'][category]!=new['fieldSHA256'][category]:
                group=('metadata' if category=='metadata' else 'credential' if category=='credential-data' else
                       'generator' if category=='job-spec' else 'authorization' if category in ('rbac','network-policy','identity-spec') else 'inventory')
                return {'hook':code,'category':group,'fieldClass':category}
        return {'hook':code,'category':'inventory','fieldClass':'object-fields'}
    # Existing exact Job/Secret hashes also remain independently mandatory.
    if baseline['keystoreHookSHA256']!=observed['keystoreHookSHA256']:return {'hook':'keystore','category':'credential','fieldClass':'object-fields'}
    if baseline['sourceGeneratorJobSHA256']!=observed['sourceGeneratorJobSHA256']:return {'hook':'cert-job','category':'generator','fieldClass':'object-fields'}
    return None

SOURCE_FAILURE_LIMIT=127*1024

CORE_DRIFT_CATEGORIES=frozenset(('namespace','workload','mesh','mixed'))
CORE_DRIFT_CHANGES=frozenset(('added','removed','changed','mixed'))

def core_failure(baseline,observed,operation):
    """Private UID/hash projections only; never Kubernetes resource bodies."""
    changed=[key for key in set(baseline)|set(observed) if baseline.get(key)!=observed.get(key)]
    require(changed,'Core inventory has no difference')
    categories={('mesh' if key=='mesh' else 'namespace' if key.startswith('namespace/') else 'workload') for key in changed}
    changes={('added' if key not in baseline else 'removed' if key not in observed else 'changed') for key in changed}
    result={'schema':1,'baseline':baseline,'observed':observed,'operation':operation,
            'diagnostic':{'category':next(iter(categories)) if len(categories)==1 else 'mixed',
                          'change':next(iter(changes)) if len(changes)==1 else 'mixed'}}
    validate_core_failure(result)
    return result

def validate_core_failure(snapshot):
    require(isinstance(snapshot,dict) and set(snapshot)=={'schema','baseline','observed','operation','diagnostic'} and
            type(snapshot['schema']) is int and snapshot['schema']==1 and
            snapshot['operation'] in ('observed','application-removed'),'Core failure schema differs')
    for name in ('baseline','observed'):
        inventory=snapshot[name]
        require(isinstance(inventory,dict) and 1<=len(inventory)<=2048,'Core inventory bound differs')
        for key,value in inventory.items():
            require(isinstance(key,str) and isinstance(value,str),'Core inventory value type differs')
            if key=='mesh':require(re.fullmatch('[a-f0-9]{64}',value),'Core mesh hash differs')
            else:
                require(re.fullmatch(r'(?:namespace/[a-z0-9][a-z0-9.-]{0,252}|[a-z0-9][a-z0-9.-]{0,252}/(?:deployments|statefulsets|daemonsets|packages\.uds\.dev)/[a-z0-9][a-z0-9.-]{0,252})',key) and
                        re.fullmatch('[A-Za-z0-9-]{1,128}',value),'Core identity projection differs')
    changed=[key for key in set(snapshot['baseline'])|set(snapshot['observed'])
             if snapshot['baseline'].get(key)!=snapshot['observed'].get(key)]
    require(changed,'Core inventory has no difference')
    categories={('mesh' if key=='mesh' else 'namespace' if key.startswith('namespace/') else 'workload') for key in changed}
    changes={('added' if key not in snapshot['baseline'] else 'removed' if key not in snapshot['observed'] else 'changed') for key in changed}
    expected={'category':next(iter(categories)) if len(categories)==1 else 'mixed',
              'change':next(iter(changes)) if len(changes)==1 else 'mixed'}
    require(snapshot['diagnostic']==expected,'Core failure diagnostic differs')
    require(len(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode())<=SOURCE_FAILURE_LIMIT,'Core failure bound')
    return snapshot

def validate_source_failure(snapshot):
    require(isinstance(snapshot,dict) and set(snapshot)=={'schema','baseline','observed','diagnostic','baselineObjects','observedObjects'} and
            type(snapshot['schema']) is int and snapshot['schema']==1,'retained hook failure schema differs')
    require(all(value is None or valid_origin(value) for value in (snapshot['baseline'],snapshot['observed'])),
            'retained hook failure origin differs')
    diagnostic=snapshot['diagnostic']
    require(isinstance(diagnostic,dict) and set(diagnostic)=={'hook','category','fieldClass'} and
            diagnostic==hook_diagnostic(snapshot['baseline'],snapshot['observed']),
            'retained hook failure diagnostic differs')
    # These actual objects exist only in the optional encrypted private file,
    # never the proof/origin persisted in the ordinary journal.
    for name in ('baseline','observed'):
        objects=snapshot[name+'Objects'];origin=snapshot[name]
        require(objects is None if origin is None else isinstance(objects,list),
                'retained hook failure objects missing')
        if origin is not None:
            try:inventory=hook_inventory(objects)
            except (ValueError,TypeError,KeyError,AttributeError,RecursionError):
                raise ValueError('retained hook failure object inventory differs') from None
            require(inventory==origin['hookInventory'],'retained hook failure object hashes differ')
    try:size=len(json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    except (TypeError,ValueError,RecursionError):raise ValueError('retained hook failure object encoding differs') from None
    require(size<=SOURCE_FAILURE_LIMIT,'retained hook failure bound')
    return snapshot

def verify_hooks(driver,baseline):
    driver._retained_source_failure=None
    validation_failed=False
    try:observed=hooks(driver)
    except (ValueError,KeyError,TypeError,AttributeError,StopIteration):
        observed=getattr(driver,'_retained_hook_observation',None)
        validation_failed=True
    if validation_failed and hook_diagnostic(baseline,observed) is None:observed=None
    diagnostic=hook_diagnostic(baseline,observed)
    if diagnostic is not None:
        failure=ValueError('stored source generator/hook changed across phase-only deployment')
        if isinstance(getattr(driver,'state',None),dict):driver.state['accepted']=False
        try:
            # Closed diagnostic plus actual objects, held only in memory for encryption.
            baseline=baseline if valid_origin(baseline) else None
            observed=observed if valid_origin(observed) else None
            held=getattr(driver,'_retained_hook_baseline',None) or {}
            baseline_objects=(held.get('objects') if baseline and held.get('inventorySHA256')==sha(baseline['hookInventory']) else None)
            driver._retained_source_failure={'schema':1,'baseline':baseline,'observed':observed,'diagnostic':diagnostic,
                                            'baselineObjects':copy.deepcopy(baseline_objects) if baseline else None,
                                            'observedObjects':copy.deepcopy(getattr(driver,'_retained_hook_objects',None)) if observed else None}
            validate_source_failure(driver._retained_source_failure)
        except Exception:
            # Optional capture must neither truncate nor mask the known drift.
            # BaseException interrupts deliberately remain uncaught.
            driver._retained_source_failure=None
        try:driver.journal('retained-hook-drift',**diagnostic)
        except Exception:pass
        raise failure
    return observed

def resource_record(obj):
    meta=obj['metadata']
    require(meta.get('namespace') in APP_RELEASES and not meta.get('deletionTimestamp'),'retained resource outside live application')
    return {'kind':obj['kind'],'name':meta['name'],'namespace':meta['namespace'],'uid':meta['uid'],'resourceVersion':meta['resourceVersion'],
            'metadataSHA256':sha({k:meta.get(k) for k in ('name','namespace','uid','labels','annotations','ownerReferences')})}

def hooks(driver):
    driver._retained_hook_observation=None
    driver._retained_hook_objects=None
    text=driver.command(['helm','--kube-context','k3d-uds','get','hooks','uds-takserver-config','-n','takserver'])
    require(len(text.encode())<=256*1024,'stored hook input bound')
    try:
        objects=[obj for obj in yaml.safe_load_all(text) if obj is not None]
        inventory=hook_inventory(objects)
        driver._retained_hook_objects=copy.deepcopy(objects)
    except (yaml.YAMLError,TypeError,KeyError,AttributeError,RecursionError):
        raise ValueError('stored hook inventory differs') from None
    secret=next(obj for obj in objects if obj.get('kind')=='Secret' and obj['metadata']['name']=='takserver-keystore')
    job=next(obj for obj in objects if obj.get('kind')=='Job' and obj['metadata']['name']=='takserver-cert-init')
    origin={'sourceFilesSHA256':source_files(),'storedHooksSHA256':hashlib.sha256(text.encode()).hexdigest(),
            'sourceGeneratorJobSHA256':sha(job),'keystoreHookSHA256':sha(secret),'hookInventory':inventory}
    driver._retained_hook_observation=origin
    for obj in objects:
        require(obj['metadata'].get('namespace') in (None,'takserver') and
                isinstance(obj['metadata'].get('annotations'),dict) and
                obj['metadata']['annotations'].get('helm.sh/hook')=='pre-install,pre-upgrade','stored hook ownership differs')
    annotations=secret['metadata'].get('annotations',{})
    require(annotations.get('helm.sh/hook')=='pre-install,pre-upgrade' and annotations.get('helm.sh/hook-delete-policy')=='before-hook-creation','stored keystore hook ownership differs')
    role=next(obj for obj in objects if obj.get('kind')=='Role' and obj['metadata']['name']=='takserver-cert-init')
    named={name for rule in role['rules'] if rule.get('resources')==['secrets'] for name in rule.get('resourceNames',[])}
    require(GENERATED_SECRETS|{'takserver-keystore'}<=named,'stored source-generator RBAC differs')
    job=next(obj for obj in objects if obj.get('kind')=='Job' and obj['metadata']['name']=='takserver-cert-init')
    require(job['spec']['template']['spec']['serviceAccountName']=='takserver-cert-init','stored source generator identity differs')
    return origin

def claim_parent(driver,pvc):
    name=pvc['metadata']['name'];namespace=pvc['metadata']['namespace'];matches=[]
    for stateful in driver.get('statefulsets',namespace=namespace)['items']:
        meta=stateful['metadata'];annotations=meta.get('annotations',{})
        if annotations.get('meta.helm.sh/release-name') not in APP_RELEASES[namespace] or annotations.get('meta.helm.sh/release-namespace')!=namespace:continue
        for template in stateful['spec'].get('volumeClaimTemplates',[]):
            prefix=template['metadata']['name']+'-'+meta['name']+'-'
            if name.startswith(prefix) and re.fullmatch(r'[0-9]+',name[len(prefix):]):matches.append((stateful,template))
    require(len(matches)==1,'PVC is not a unique claim of the installed application chart')
    parent,template=matches[0]
    require(pvc['spec']['storageClassName']=='local-path-local' and pvc['status']['phase']=='Bound','PVC is not the disposable local trial data')
    volume=driver.get('persistentvolume',pvc['spec']['volumeName'],namespace='default')
    claim=volume['spec']['claimRef'];require(claim['namespace']==namespace and claim['name']==name and claim['uid']==pvc['metadata']['uid'],'PV claim ownership differs')
    return {'statefulSet':parent['metadata']['name'],'statefulSetUID':parent['metadata']['uid'],'claimTemplateSHA256':sha(template),
            'volumeName':volume['metadata']['name'],'volumeUID':volume['metadata']['uid']}

def mount(driver,pvc):
    require(pvc['metadata']['namespace'] in APP_RELEASES,'PVC mount outside application')
    matches=[];parent=claim_parent(driver,pvc)
    for pod in driver.get('pods',namespace=pvc['metadata']['namespace'])['items']:
        volumes={v['name'] for v in pod['spec'].get('volumes',[]) if v.get('persistentVolumeClaim',{}).get('claimName')==pvc['metadata']['name']}
        if volumes:
            owners=[x for x in pod['metadata'].get('ownerReferences',[]) if x.get('controller') is True]
            require(len(owners)==1 and owners[0].get('kind')=='StatefulSet' and owners[0].get('uid')==parent['statefulSetUID'],'PVC consumer is not its exact application controller')
        for container in pod['spec']['containers']:
            for item in container.get('volumeMounts',[]):
                if item['name'] in volumes and not item.get('readOnly'):
                    path=item['mountPath'];require(re.fullmatch(r'/[A-Za-z0-9_./-]+',path) and '..' not in PurePosixPath(path).parts,'unsupported PVC mount path')
                    require(isinstance(container.get('name'),str) and re.fullmatch(r'[a-z0-9][a-z0-9-]*',container['name']),'unsupported PVC container')
                    matches.append((pod['metadata']['name'],container['name'],path))
    require(len(matches)==1,'PVC has no unique writable application mount')
    return matches[0]

def sentinel_exec(driver,operation,namespace,pod,container,args,payload=None):
    require(operation in ('create','read','remove') and namespace in APP_RELEASES,'unsupported sentinel operation/scope')
    driver.journal('retained-sentinel-start',operation=operation,namespace=namespace)
    result=driver.exec(pod,container,args,payload,namespace=namespace)
    # Fixed bounded facts only: never publish remote stderr, paths or Pod names.
    status=result.returncode
    driver.journal('retained-sentinel-exec',operation=operation,namespace=namespace,
                   exitStatus=status if isinstance(status,int) and -255<=status<=255 else None,
                   stderrPresent=bool(result.stderr.strip()))
    return result

def sentinel_create_script(path,sentinel):
    # Fixed predicate statuses: 61 missing directory, 62 symlink, 63 unwritable,
    # 64 existing sentinel, 65 write failure. No remote text is evidence.
    directory=shlex.quote(path);target=shlex.quote(sentinel)
    return ('umask 077; set -o noclobber; '
            'if test -L '+directory+'; then exit 62; fi; '
            'if ! test -d '+directory+'; then exit 61; fi; '
            'if ! test -w '+directory+'; then exit 63; fi; '
            'if test -e '+target+' || test -L '+target+'; then exit 64; fi; '
            'if ! cat > '+target+'; then exit 65; fi')

def capture(driver):
    driver._retained_hook_baseline=None
    origin=hooks(driver);records=[]
    driver._retained_hook_baseline={'inventorySHA256':sha(origin['hookInventory']),
                                    'objects':copy.deepcopy(driver._retained_hook_objects)}
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        for pvc in driver.get('persistentvolumeclaims',namespace=namespace)['items']:
            rec=resource_record(pvc);rec['parent']=claim_parent(driver,pvc);pod,container,path=mount(driver,pvc)
            sentinel=path.rstrip('/')+'/.uds-'+driver.state['trialID'];token=sha({'uid':rec['uid'],'trial':driver.state['trialID']})+'\n'
            script=sentinel_create_script(path,sentinel)
            result=sentinel_exec(driver,'create',namespace,pod,container,['/bin/bash','-ceu',script],token)
            require(result.returncode==0 and not result.stderr.strip(),'could not create exact owned PVC data sentinel')
            rec.update(sentinel=sentinel,sentinelSHA256=hashlib.sha256(token.encode()).hexdigest());records.append(rec)
    require(records,'application data claims missing')
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        referenced=set()
        for pod in driver.get('pods',namespace=namespace)['items']:
            for volume in pod['spec'].get('volumes',[]):
                if volume.get('secret'):referenced.add(volume['secret']['secretName'])
                for item in volume.get('projected',{}).get('sources',[]):
                    if item.get('secret'):referenced.add(item['secret']['name'])
            for container in [*pod['spec'].get('containers',[]),*pod['spec'].get('initContainers',[])]:
                for env in container.get('env',[]):
                    if env.get('valueFrom',{}).get('secretKeyRef'):referenced.add(env['valueFrom']['secretKeyRef']['name'])
                for env in container.get('envFrom',[]):
                    if env.get('secretRef'):referenced.add(env['secretRef']['name'])
        for secret in driver.get('secrets',namespace=namespace)['items']:
            name=secret['metadata']['name']
            annotations=secret['metadata'].get('annotations',{})
            extra=(namespace,name) in {('cloudtak','cloudtak-tak-admin'),('takserver','takclient-enrollment'),('takserver','takserver-crl')}
            if extra:generated_secret(driver,secret)
            if name in referenced or namespace=='takserver' and name in GENERATED_SECRETS|{'takserver-keystore'} or extra:
                rec=resource_record(secret);rec['dataSHA256']=sha(secret.get('data',{}));rec['recreatedHook']=namespace=='takserver' and name=='takserver-keystore'
                if namespace=='takserver' and name=='takserver-crl':rec['emptyCrlDataSHA256']=rec['dataSHA256']
                records.append(rec)
    for config in driver.get('configmaps')['items']:
        if config['metadata']['name']=='takserver-startup':startup_map(config)
        image_binding=ignite_binding(driver,config) if config['metadata']['name']=='ignite-extensions' else {}
        if config['metadata']['name'] in ('cert-migration','takserver-startup','ignite-extensions'):
            rec=resource_record(config);rec['dataSHA256']=sha({'data':config.get('data',{}),'binaryData':config.get('binaryData',{})});rec.update(image_binding);records.append(rec)
    require(any(x['name']=='takserver-keystore' for x in records) and any(x['name']=='cert-migration' for x in records),'source-generated credential state missing')
    return {'source':origin,'records':records}

def verify(driver,proof,remove_sentinels=False):
    verify_hooks(driver,proof['source'])
    observations=[]
    for rec in proof['records']:
        current=driver.get(rec['kind'],rec['name'],namespace=rec['namespace']);meta=current['metadata']
        if not rec.get('recreatedHook'):require(meta['uid']==rec['uid'],'application invariant resource replaced')
        else:require(meta.get('annotations',{}).get('helm.sh/hook-delete-policy')=='before-hook-creation','keystore no longer source-owned hook')
        if rec['kind']=='PersistentVolumeClaim':
            require(claim_parent(driver,current)==rec['parent'],'data PVC/PV parent changed');pod,container,_=mount(driver,current)
            result=sentinel_exec(driver,'read',rec['namespace'],pod,container,['/bin/bash','-ceu','test ! -L '+shlex.quote(rec['sentinel'])+'; sha256sum -- '+shlex.quote(rec['sentinel'])])
            observed=result.stdout.split()
            require(result.returncode==0 and not result.stderr.strip() and len(observed)==2 and observed[0]==rec['sentinelSHA256'] and observed[1]==rec['sentinel'],'application PVC data sentinel changed')
            if remove_sentinels:
                result=sentinel_exec(driver,'remove',rec['namespace'],pod,container,['/bin/bash','-ceu','rm -- '+shlex.quote(rec['sentinel'])]);require(result.returncode==0,'exact owned sentinel cleanup failed')
            observations.append({'kind':rec['kind'],'name':rec['name'],'namespace':rec['namespace'],'uid':meta['uid'],'volumeUID':rec['parent']['volumeUID'],'sentinelSHA256':observed[0]})
        else:
            value=current.get('data',{}) if rec['kind']=='Secret' else {'data':current.get('data',{}),'binaryData':current.get('binaryData',{})}
            require(sha(value)==rec['dataSHA256'],'application credential/certificate data changed')
            if rec['kind']=='ConfigMap' and rec['name']=='ignite-extensions':
                require(ignite_binding(driver,current)=={key:rec[key] for key in ('imageBoundJarSHA256','imageSourceSHA256')},'bound Ignite image source changed')
            observations.append({'kind':rec['kind'],'name':rec['name'],'namespace':rec['namespace'],'uid':meta['uid'],'dataSHA256':sha(value),'recreatedHook':rec.get('recreatedHook',False)})


    return observations

def cleanup_snapshot(driver,proof):
    if any(rec['namespace']=='takserver' and rec['name'] in GENERATED_SECRETS|{'takserver-crl'} for rec in proof['records']):no_live_clients(driver)
    verify(driver,proof,remove_sentinels=True);result=[]
    for rec in proof['records']:
        if cleanup_allowed(rec):
            current=(json.loads(driver.kube('get',rec['kind'],rec['name'],'-n',rec['namespace'],
                '--show-managed-fields=true','-o','json')) if rec['kind']=='PersistentVolumeClaim' else
                driver.get(rec['kind'],rec['name'],namespace=rec['namespace']))
            if rec['name']=='takserver-startup':startup_map(current)
            if rec['name'] in ('cloudtak-tak-admin','takclient-enrollment','takserver-crl'):generated_secret(driver,current,cleanup=True)
            item=resource_record(current);item['origin']=proof['source']
            if rec['namespace']=='takserver' and rec['name']=='takserver-crl':
                require(sha(current.get('data',{}))==rec['emptyCrlDataSHA256'],'verified empty CRL data changed')
                item['emptyCrlDataSHA256']=rec['emptyCrlDataSHA256']
            if rec['kind']=='ConfigMap' and rec['name']=='ignite-extensions':
                require(item['uid']==rec['uid'],'verified Ignite map replaced before cleanup snapshot')
                require(ignite_jar(current)==rec['imageBoundJarSHA256'],'verified Ignite binary changed')
                item.update({key:rec[key] for key in ('imageBoundJarSHA256','imageSourceSHA256')})
            if rec['kind']=='PersistentVolumeClaim':
                require(item['uid']==rec['uid'],'verified PVC replaced before cleanup snapshot')
                item['parent']=claim_parent(driver,current)
                require(item['parent']==rec['parent'],'verified PVC parent changed before cleanup snapshot')
                barrier=pvc_usage_barrier(current)
                if barrier is not None:item['pvcUsageBarrier']=barrier
                try:item['pvcState']=pvc_state(current)
                except Exception:pass  # Optional evidence cannot change cleanup authorization.
            result.append(item)
    return result

CLEAN_INVENTORY_KINDS={'deployments':'Deployment','statefulsets':'StatefulSet','jobs':'Job','pods':'Pod',
    'persistentvolumeclaims':'PersistentVolumeClaim','secrets':'Secret','configmaps':'ConfigMap','services':'Service',
    'serviceaccounts':'ServiceAccount','roles':'Role','rolebindings':'RoleBinding','packages.uds.dev':'Package'}
CLEAN_INVENTORY_LIMIT=16

def validate_clean_inventory_failure(snapshot):
    require(isinstance(snapshot,dict) and set(snapshot)=={'schema','namespace','kind','reason','totalCount',
            'truncated','inventorySHA256','objects'} and type(snapshot['schema']) is int and snapshot['schema']==1,
            'clean inventory schema differs')
    ns,kind=snapshot['namespace'],snapshot['kind']
    require(ns in (*APP_RELEASES,'pepr-system') and kind in CLEAN_INVENTORY_KINDS and
            (ns!='pepr-system' or kind in ('deployments','secrets')),'clean inventory scope differs')
    require(snapshot['reason'] in ('unexpected-objects','preserved-trust-changed') and
            (snapshot['reason']!='preserved-trust-changed' or ns in APP_RELEASES and kind=='configmaps'),
            'clean inventory reason differs')
    total=snapshot['totalCount'];objects=snapshot['objects']
    require(type(total) is int and 1<=total<=4096 and isinstance(objects,list) and
            len(objects)==min(total,CLEAN_INVENTORY_LIMIT) and type(snapshot['truncated']) is bool and
            snapshot['truncated']==(total>CLEAN_INVENTORY_LIMIT),'clean inventory count differs')
    require(isinstance(snapshot['inventorySHA256'],str) and re.fullmatch('[a-f0-9]{64}',snapshot['inventorySHA256']),
            'clean inventory digest differs')
    names=[]
    for obj in objects:
        require(isinstance(obj,dict) and set(obj)=={'name','uid','resourceVersion','deleting','objectSHA256'},
                'clean inventory object differs')
        for key in ('name','uid','resourceVersion'):
            require(isinstance(obj[key],str) and re.fullmatch('[A-Za-z0-9._-]{1,253}',obj[key]),
                    'clean inventory identity differs')
        require(type(obj['deleting']) is bool and isinstance(obj['objectSHA256'],str) and
                re.fullmatch('[a-f0-9]{64}',obj['objectSHA256']),'clean inventory object state differs')
        if snapshot['reason']=='preserved-trust-changed':
            require(obj['name'] in ('kube-root-ca.crt','istio-ca-root-cert','istio-ca-crl','uds-trust-bundle'),
                    'clean inventory trust name differs')
        names.append(obj['name'])
    require(names==sorted(set(names)),'clean inventory ordering or duplicate differs')
    if not snapshot['truncated']:require(sha(objects)==snapshot['inventorySHA256'],'clean inventory digest mismatch')
    require(len(json.dumps(snapshot).encode())<=SOURCE_FAILURE_LIMIT,'clean inventory byte bound')
    return snapshot

def clean_inventory_failure(namespace,kind,objects,reason='unexpected-objects'):
    require(kind in CLEAN_INVENTORY_KINDS and isinstance(objects,list) and 1<=len(objects)<=4096,
            'clean inventory query bound differs')
    records=[]
    for obj in objects:
        meta=obj['metadata']
        require(obj.get('kind')==CLEAN_INVENTORY_KINDS[kind] and meta.get('namespace')==namespace,
                'clean inventory query scope differs')
        records.append({'name':meta['name'],'uid':meta['uid'],'resourceVersion':meta['resourceVersion'],
            'deleting':bool(meta.get('deletionTimestamp')),'objectSHA256':sha(obj)})
    records.sort(key=lambda item:item['name'])
    require(len({item['name'] for item in records})==len(records),'duplicate clean inventory query object')
    return validate_clean_inventory_failure({'schema':1,'namespace':namespace,'kind':kind,'reason':reason,
        'totalCount':len(records),'truncated':len(records)>CLEAN_INVENTORY_LIMIT,
        'inventorySHA256':sha(records),'objects':records[:CLEAN_INVENTORY_LIMIT]})

def validate_consumer_failure(snapshot):
    require(isinstance(snapshot,dict) and set(snapshot)=={'schema','consumers'} and
            type(snapshot['schema']) is int and snapshot['schema']==1,'consumer failure schema differs')
    records=snapshot['consumers']
    require(isinstance(records,list) and 1<=len(records)<=128,'consumer inventory bound differs')
    seen=set()
    for rec in records:
        require(isinstance(rec,dict) and set(rec)=={'kind','namespace','name','uid','resourceVersion','deleting','phase','owners'},'consumer record differs')
        require(rec['kind'] in ('Pod','Deployment','StatefulSet') and rec['namespace'] in APP_RELEASES,'consumer scope differs')
        for key in ('name','uid','resourceVersion'):
            require(isinstance(rec[key],str) and re.fullmatch('[A-Za-z0-9._-]{1,253}',rec[key]),'consumer identity differs')
        identity=(rec['namespace'],rec['kind'],rec['name'])
        require(identity not in seen,'duplicate consumer');seen.add(identity)
        require(type(rec['deleting']) is bool and rec['phase'] in ('','Pending','Running','Succeeded','Failed','Unknown'),'consumer state differs')
        require(isinstance(rec['owners'],list) and len(rec['owners'])<=8,'consumer owner bound differs')
        for owner in rec['owners']:
            require(isinstance(owner,dict) and set(owner)=={'apiVersion','kind','name','uid'},'consumer owner schema differs')
            for key,value in owner.items():
                require(isinstance(value,str) and re.fullmatch('[A-Za-z0-9./_-]{1,253}',value),'consumer owner identity differs')
    require(len(json.dumps(snapshot).encode())<=SOURCE_FAILURE_LIMIT,'consumer failure byte bound')
    return snapshot

def consumer_failure(driver):
    records=[]
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        for kind in ('pods','deployments','statefulsets'):
            for obj in driver.get(kind,namespace=namespace)['items']:
                meta=obj['metadata']
                require(meta.get('namespace')==namespace and obj.get('kind')==
                        {'pods':'Pod','deployments':'Deployment','statefulsets':'StatefulSet'}[kind],
                        'consumer query scope differs')
                records.append({'kind':obj['kind'],'namespace':namespace,'name':meta['name'],
                    'uid':meta['uid'],'resourceVersion':meta['resourceVersion'],
                    'deleting':bool(meta.get('deletionTimestamp')),
                    'phase':obj.get('status',{}).get('phase','') if obj['kind']=='Pod' else '',
                    'owners':[{key:owner[key] for key in ('apiVersion','kind','name','uid')}
                              for owner in meta.get('ownerReferences',[])]})
                require(len(records)<=128,'consumer inventory bound differs')
    return validate_consumer_failure({'schema':1,'consumers':records})

def consumers_gone(driver):
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        require(all(not driver.get(kind,namespace=namespace)['items'] for kind in ('pods','deployments','statefulsets')),'application still has consumers')
    return {'consumersGone':True}

CLEANUP_IDENTITY_FIELDS=('kind','name','namespace','uid','resourceVersion','metadataSHA256')

# Fixed API field names only: unknown status keys/values are represented by one
# aggregate digest. Presence is hashed too, so missing and explicit null differ.
PVC_STATUS_FIELDS=('phase','accessModes','capacity','conditions','allocatedResources',
                   'resizeStatus','allocatedResourceStatuses',
                   'currentVolumeAttributesClassName','modifyVolumeStatus')
PVC_STATUS_DIGEST_KEYS=set(PVC_STATUS_FIELDS)|{'other'}
PVC_CONDITION_TYPES={'Resizing','FileSystemResizePending','ControllerResizeError',
                     'NodeResizeError','ModifyVolumeError','ModifyingVolume','Unused'}
PVC_CONDITION_STATUSES={'True','False','Unknown','Other'}
PVC_CONDITION_FIELD_GROUPS={'type':('type',),'status':('status',),
    'detail':('reason','message'),'timestamps':('lastProbeTime','lastTransitionTime')}
PVC_CONDITION_DIGEST_KEYS=set(PVC_CONDITION_FIELD_GROUPS)|{'other'}
# Field-manager strings are hints, not authenticated controller identities.
PVC_MANAGER_CODES={'k3s':'K3s','kube-controller-manager':'KubeController','kubelet':'Kubelet',
    'velero':'Velero','pepr':'Pepr','zarf':'Zarf','local-path-provisioner':'LocalPath',
    'kubectl-patch':'KubectlPatch','kubectl-edit':'KubectlEdit','kubectl-client-side-apply':'KubectlApply'}

def validate_condition_owners(value):
    require(isinstance(value,dict) and set(value)=={'count','codes','unknownCount','sha256'},'PVC managed owner schema differs')
    require(type(value['count']) is int and 0<=value['count']<=32 and type(value['unknownCount']) is int and
            0<=value['unknownCount']<=value['count'],'PVC managed owner bound differs')
    codes=value['codes']
    require(isinstance(codes,list) and all(isinstance(code,str) and code in set(PVC_MANAGER_CODES.values())|{'Other'} for code in codes) and
            codes==sorted(set(codes)) and len(codes)<=value['count'] and bool(codes)==bool(value['count']) and
            ('Other' in codes)==bool(value['unknownCount']),'PVC managed owner code differs')
    require(isinstance(value['sha256'],str) and re.fullmatch('[0-9a-f]{64}',value['sha256']),'PVC managed owner digest differs')
    return value

def condition_owners(meta):
    entries=meta['managedFields']
    require(isinstance(entries,list) and len(entries)<=32 and all(isinstance(entry,dict) for entry in entries) and
            len(json.dumps(entries).encode())<=64*1024,'PVC managed field input bound differs')
    relevant=[];codes=set();unknown=0
    for entry in entries:
        fields=entry.get('fieldsV1')
        status=fields.get('f:status') if isinstance(fields,dict) else None
        if entry.get('fieldsType')!='FieldsV1' or not isinstance(status,dict) or not isinstance(status.get('f:conditions'),dict):continue
        manager=entry.get('manager');code=PVC_MANAGER_CODES.get(manager,'Other') if isinstance(manager,str) else 'Other'
        codes.add(code);unknown+=code=='Other'
        relevant.append({'manager':manager,'operation':entry.get('operation'),'subresource':entry.get('subresource'),
                         'conditions':status['f:conditions']})
    # No raw manager, condition key, operation, timestamp or value is emitted.
    return validate_condition_owners({'count':len(relevant),'codes':sorted(codes),'unknownCount':unknown,'sha256':sha(relevant)})

def validate_condition_facts(value):
    require(isinstance(value,dict) and {'shape','count','knownTypes','otherTypeCount'}<=set(value) and
            set(value)<={'shape','count','knownTypes','otherTypeCount','fieldsSHA256','managedOwners'},'PVC condition facts schema differs')
    require(value['shape'] in ('absent','null','list','other') and type(value['count']) is int and
            0<=value['count']<=32 and type(value['otherTypeCount']) is int and
            0<=value['otherTypeCount']<=value['count'],'PVC condition facts bound differs')
    known=value['knownTypes']
    require(isinstance(known,dict) and set(known)<=PVC_CONDITION_TYPES,'PVC condition type differs')
    for statuses in known.values():
        require(isinstance(statuses,list) and 1<=len(statuses)<=32 and
                all(isinstance(status,str) and status in PVC_CONDITION_STATUSES for status in statuses) and
                statuses==sorted(statuses),'PVC condition status differs')
    require(sum(map(len,known.values()))+value['otherTypeCount']==value['count'],'PVC condition count differs')
    require(value['shape']=='list' or value['count']==0,'PVC condition shape differs')
    if 'fieldsSHA256' in value:
        fields=value['fieldsSHA256']
        require(value['shape']=='list' and isinstance(fields,dict) and
                set(fields)==PVC_CONDITION_DIGEST_KEYS,'PVC condition field schema differs')
        require(all(isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest)
                    for digest in fields.values()),'PVC condition field digest differs')
    if 'managedOwners' in value:validate_condition_owners(value['managedOwners'])
    return value

def condition_facts(status):
    value=status.get('conditions')
    shape='absent' if 'conditions' not in status else 'null' if value is None else 'list' if isinstance(value,list) else 'other'
    result={'shape':shape,'count':0,'knownTypes':{},'otherTypeCount':0}
    if shape=='list':
        require(len(value)<=32 and all(isinstance(item,dict) for item in value),'PVC condition input bound differs')
        result['count']=len(value)
        for item in value:
            kind=item.get('type')
            if not isinstance(kind,str) or kind not in PVC_CONDITION_TYPES:
                result['otherTypeCount']+=1
                continue
            state=item.get('status')
            if not isinstance(state,str) or state not in PVC_CONDITION_STATUSES:state='Other'
            result['knownTypes'].setdefault(kind,[]).append(state)
        for statuses in result['knownTypes'].values():statuses.sort()
        # Fixed group names and digests only; arbitrary condition values/keys
        # never leave the input object. Preserve missing/null and list ordering.
        result['fieldsSHA256']={group:sha([{key:{'present':key in item,'value':item.get(key)}
            for key in keys} for item in value]) for group,keys in PVC_CONDITION_FIELD_GROUPS.items()}
        known_fields={key for keys in PVC_CONDITION_FIELD_GROUPS.values() for key in keys}
        result['fieldsSHA256']['other']=sha([{key:entry for key,entry in item.items()
            if key not in known_fields} for item in value])
    return validate_condition_facts(result)

def validate_pvc_state(value):
    require(isinstance(value,dict) and set(value) in ({'specSHA256','statusSHA256','finalizersSHA256','pvcProtection','otherFinalizerCount'},
            {'specSHA256','statusSHA256','finalizersSHA256','pvcProtection','otherFinalizerCount','statusFieldsSHA256'},
            {'specSHA256','statusSHA256','finalizersSHA256','pvcProtection','otherFinalizerCount','statusFieldsSHA256','conditionFacts'}),'PVC state schema differs')
    for key in ('specSHA256','statusSHA256','finalizersSHA256'):
        require(isinstance(value[key],str) and re.fullmatch('[0-9a-f]{64}',value[key]),'PVC state digest differs')
    require(type(value['pvcProtection']) is bool and type(value['otherFinalizerCount']) is int and
            0<=value['otherFinalizerCount']<=32,'PVC finalizer facts differ')
    if 'statusFieldsSHA256' in value:
        fields=value['statusFieldsSHA256']
        require(isinstance(fields,dict) and set(fields)==PVC_STATUS_DIGEST_KEYS,'PVC status field schema differs')
        require(all(isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest)
                    for digest in fields.values()),'PVC status field digest differs')
    if 'conditionFacts' in value:validate_condition_facts(value['conditionFacts'])
    return value

def pvc_state(obj):
    require(obj.get('kind')=='PersistentVolumeClaim' and obj['metadata'].get('namespace') in APP_RELEASES,'PVC diagnostic scope differs')
    finalizers=obj['metadata'].get('finalizers',[])
    require(isinstance(finalizers,list) and len(finalizers)<=32 and all(isinstance(value,str) and
            re.fullmatch('[A-Za-z0-9./_-]{1,253}',value) for value in finalizers),'PVC finalizer input differs')
    require(len(json.dumps({'spec':obj.get('spec',{}),'status':obj.get('status',{})}).encode())<=64*1024,'PVC diagnostic input bound')
    status=obj.get('status',{})
    require(isinstance(status,dict),'PVC status input differs')
    fields={key:sha({'present':key in status,'value':status.get(key)}) for key in PVC_STATUS_FIELDS}
    fields['other']=sha({key:value for key,value in status.items() if key not in PVC_STATUS_FIELDS})
    state={'statusFieldsSHA256':fields,'specSHA256':sha(obj.get('spec',{})),'statusSHA256':sha(obj.get('status',{})),
        'finalizersSHA256':sha(sorted(finalizers)),'pvcProtection':'kubernetes.io/pvc-protection' in finalizers,
        'otherFinalizerCount':sum(value!='kubernetes.io/pvc-protection' for value in finalizers)}
    try:state['conditionFacts']=condition_facts(status)
    except ValueError:pass  # Optional detail cannot suppress the existing fingerprints.
    if 'conditionFacts' in state and 'managedFields' in obj['metadata']:
        try:state['conditionFacts']['managedOwners']=condition_owners(obj['metadata'])
        except Exception:pass  # Optional hint failure never suppresses existing fingerprints.
    return validate_pvc_state(state)

def validate_cleanup_drift(snapshot):
    require(isinstance(snapshot,dict) and set(snapshot) in ({'schema','expected','observed','changedFields'}, {'schema','expected','observed','changedFields','pvcState'}) and
            type(snapshot['schema']) is int and snapshot['schema']==1,'cleanup drift schema differs')
    for key in ('expected','observed'):
        rec=snapshot[key]
        require(isinstance(rec,dict) and set(rec)==set(CLEANUP_IDENTITY_FIELDS),'cleanup drift record differs')
        require(rec['namespace'] in APP_RELEASES and (rec['kind']=='PersistentVolumeClaim' or
                (rec['namespace'],rec['kind'],rec['name']) in SOURCE_CLEANUP),'cleanup drift scope differs')
        for field in ('name','uid','resourceVersion'):
            require(isinstance(rec[field],str) and re.fullmatch('[A-Za-z0-9._-]{1,253}',rec[field]),'cleanup drift identity differs')
        require(isinstance(rec['metadataSHA256'],str) and re.fullmatch('[0-9a-f]{64}',rec['metadataSHA256']),'cleanup drift digest differs')
    changed=[key for key in CLEANUP_IDENTITY_FIELDS if snapshot['expected'][key]!=snapshot['observed'][key]]
    require(changed and snapshot['changedFields']==changed,'cleanup drift fields differ')
    if 'pvcState' in snapshot:
        require(snapshot['expected']['kind']==snapshot['observed']['kind']=='PersistentVolumeClaim' and
                isinstance(snapshot['pvcState'],dict) and set(snapshot['pvcState'])=={'expected','observed'},'PVC state envelope differs')
        for value in snapshot['pvcState'].values():validate_pvc_state(value)
    require(len(json.dumps(snapshot).encode())<=4096,'cleanup drift byte bound')
    return snapshot

def capture_cleanup_drift(driver,rec,current):
    expected={key:rec[key] for key in CLEANUP_IDENTITY_FIELDS}
    try:observed=resource_record(current)
    except Exception:
        driver._retained_cleanup_drift=None;return
    # Record only identities and a metadata digest, never object bodies or values.
    # Capture failure must not mask the deletion refusal or authorize a retry.
    try:
        snapshot={'schema':1,'expected':expected,'observed':observed,
            'changedFields':[key for key in CLEANUP_IDENTITY_FIELDS if expected[key]!=observed[key]]}
        if rec['kind']=='PersistentVolumeClaim' and 'pvcState' in rec:
            try:
                baseline=copy.deepcopy(validate_pvc_state(rec['pvcState']));current_state=pvc_state(current)
                snapshot['pvcState']={'expected':baseline,'observed':current_state}
                if len(json.dumps(snapshot).encode())>4096:
                    for state in snapshot['pvcState'].values():state.get('conditionFacts',{}).pop('fieldsSHA256',None)
                if len(json.dumps(snapshot).encode())>4096:
                    for state in snapshot['pvcState'].values():state.get('conditionFacts',{}).pop('managedOwners',None)
                if len(json.dumps(snapshot).encode())>4096:
                    for state in snapshot['pvcState'].values():state.pop('conditionFacts',None)
            except Exception:pass  # Keep the basic identity diagnostic if extra capture fails.
        driver._retained_cleanup_drift=validate_cleanup_drift(snapshot)
    except Exception:driver._retained_cleanup_drift=None

# The controller's published in-use -> unused transition is an expected phase
# boundary. This separate proof is authorization state, not optional diagnostics.
# It binds every PVC field except RV/managedFields and the one exact known
# condition. No manager string grants permission; deletion still binds UID + RV.
def pvc_usage_condition(obj,state):
    status=obj.get('status',{})
    conditions=status.get('conditions')
    if obj.get('kind')!='PersistentVolumeClaim' or status.get('phase')!='Bound' or not isinstance(conditions,list) or len(conditions)!=1:return False
    value=conditions[0]
    required={'type','status','reason','message','lastTransitionTime'}
    if not isinstance(value,dict) or not required<=set(value) or not set(value)<=required|{'lastProbeTime'}:return False
    reason,message=(('PodUsingPVC','A pod is currently referencing this PVC') if state=='False' else
                    ('NoPodsUsingPVC','No pods are currently referencing this PVC'))
    if state not in ('False','True') or any(value.get(key)!=expected for key,expected in
        (('type','Unused'),('status',state),('reason',reason),('message',message))):return False
    for key in ('lastTransitionTime','lastProbeTime'):
        stamp=value.get(key)
        if key=='lastProbeTime' and stamp is None:continue
        if not isinstance(stamp,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',stamp):return False
        try:datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError:return False
    return True

def pvc_usage_invariant(obj):
    value=copy.deepcopy(obj)
    value['metadata'].pop('resourceVersion',None)
    value['metadata'].pop('managedFields',None)
    value['status'].pop('conditions',None)
    return sha(value)

def pvc_usage_barrier(obj):
    if not pvc_usage_condition(obj,'False'):return None
    return {'schema':1,'transition':'UnusedFalseToTrue','invariantSHA256':pvc_usage_invariant(obj)}

def validate_pvc_usage_barrier(value):
    require(isinstance(value,dict) and set(value)=={'schema','transition','invariantSHA256'} and
            type(value['schema']) is int and value['schema']==1 and value['transition']=='UnusedFalseToTrue' and
            isinstance(value['invariantSHA256'],str) and re.fullmatch('[0-9a-f]{64}',value['invariantSHA256']),
            'PVC cleanup barrier differs')
    return value

def quiescent_pvc_record(rec,current):
    barrier=validate_pvc_usage_barrier(rec['pvcUsageBarrier'])
    require(rec['kind']=='PersistentVolumeClaim','PVC cleanup barrier scope differs')
    observed=resource_record(current)
    require(all(observed[key]==rec[key] for key in CLEANUP_IDENTITY_FIELDS if key!='resourceVersion'),
            'retained PVC identity/metadata changed before quiescence')
    require(pvc_usage_invariant(current)==barrier['invariantSHA256'],
            'retained PVC invariant changed before quiescence')
    if pvc_usage_condition(current,'False'):return None
    require(pvc_usage_condition(current,'True'),'retained PVC usage transition differs')
    require(observed['resourceVersion']!=rec['resourceVersion'],'PVC usage transition has unchanged resource version')
    result=copy.deepcopy(rec);result.update(observed);result.pop('pvcUsageBarrier')
    result.pop('pvcState',None)
    try:result['pvcState']=pvc_state(current)
    except Exception:pass  # Optional evidence cannot change cleanup authorization.
    return result

def quiescent_cleanup_record(driver,rec):
    if 'pvcUsageBarrier' not in rec:return rec
    require(cleanup_allowed(rec) and rec['kind']=='PersistentVolumeClaim','PVC cleanup barrier scope differs')
    require_origin(rec);validate_pvc_usage_barrier(rec['pvcUsageBarrier'])
    deadline=time.monotonic()+60
    while True:
        driver.guard();driver.core_unchanged();consumers_gone(driver)
        if not driver.kube('get','namespace',rec['namespace'],'--ignore-not-found','-o','json').strip():return rec
        text=driver.kube('get',rec['kind'],rec['name'],'-n',rec['namespace'],'--ignore-not-found',
                         '--show-managed-fields=true','-o','json')
        if not text.strip():return rec
        current=json.loads(text)
        try:result=quiescent_pvc_record(rec,current)
        except ValueError:
            capture_cleanup_drift(driver,rec,current)
            raise
        if result is not None:return result
        require(time.monotonic()<deadline,'retained PVC usage condition did not settle')
        time.sleep(min(1,max(0,deadline-time.monotonic())))

def conditional_delete(driver,rec):
    require(cleanup_allowed(rec),'cleanup outside exact retained application scope')
    require_origin(rec)
    driver.guard();driver.core_unchanged()
    consumers_gone(driver)
    if rec['namespace']=='takserver' and rec['name'] in GENERATED_SECRETS|{'takserver-crl'}:no_live_clients(driver)
    if not driver.kube('get','namespace',rec['namespace'],'--ignore-not-found','-o','json').strip():return
    output=driver.kube('get',rec['kind'],rec['name'],'-n',rec['namespace'],'--ignore-not-found',
        *(['--show-managed-fields=true'] if rec['kind']=='PersistentVolumeClaim' else []),'-o','json')
    if not output.strip():return
    current=json.loads(output)
    expected={key:rec[key] for key in CLEANUP_IDENTITY_FIELDS};observed=resource_record(current)
    if observed!=expected:
        capture_cleanup_drift(driver,rec,current)
        raise ValueError('retained resource identity/metadata changed')
    if (rec['namespace'],rec['kind'],rec['name'])==('takserver','Secret','takserver-crl'):
        require(sha(current.get('data',{}))==rec['emptyCrlDataSHA256'],'verified empty CRL data changed')
    if (rec['namespace'],rec['kind'],rec['name'])==('takserver','ConfigMap','ignite-extensions'):
        require(ignite_jar(current)==rec['imageBoundJarSHA256'],'verified Ignite binary changed')
    if rec['kind']=='PersistentVolumeClaim':
        volume=driver.get('persistentvolume',rec['parent']['volumeName'],namespace='default')
        require(volume['metadata']['uid']==rec['parent']['volumeUID'] and volume['spec']['claimRef']['uid']==rec['uid'] and volume['spec']['claimRef']['namespace']==rec['namespace'] and volume['spec']['claimRef']['name']==rec['name'],'retained PVC volume ownership changed')
    delete_record(driver,rec)


def delete_record(driver,rec):
    config=json.loads(driver.command(['kubectl','--context','k3d-uds','config','view','--minify','--raw','-o','json']))
    require(len(config['clusters'])==len(config['users'])==1,'ambiguous local delete client')
    cluster,user=config['clusters'][0]['cluster'],config['users'][0]['user'];host=urllib.parse.urlsplit(cluster['server'])
    require(host.scheme=='https' and host.hostname in ('127.0.0.1','localhost','0.0.0.0','::1') and not host.username,'cleanup endpoint is not local')
    require(set(user)=={'client-certificate-data','client-key-data'},'cleanup requires local certificate client')
    context=ssl.create_default_context(cadata=base64.b64decode(cluster['certificate-authority-data'],validate=True).decode())
    with tempfile.TemporaryDirectory(prefix='owned-app-cleanup-') as temp:
        cert,key=Path(temp)/'client.crt',Path(temp)/'client.key'
        for path,field in ((cert,'client-certificate-data'),(key,'client-key-data')):path.write_bytes(base64.b64decode(user[field],validate=True));path.chmod(0o600)
        context.load_cert_chain(cert,key)
        plural={'Secret':'secrets','ConfigMap':'configmaps','PersistentVolumeClaim':'persistentvolumeclaims','Job':'jobs'}[rec['kind']]
        prefix='/apis/batch/v1' if rec['kind']=='Job' else '/api/v1'
        endpoint=cluster['server'].rstrip('/')+prefix+'/namespaces/'+rec['namespace']+'/'+plural+'/'+urllib.parse.quote(rec['name'],safe='')
        options={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':rec['uid'],'resourceVersion':rec['resourceVersion']},'propagationPolicy':'Foreground'}
        request=urllib.request.Request(endpoint,data=json.dumps(options).encode(),method='DELETE',headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context))
        with opener.open(request,timeout=30) as response:require(response.status in (200,202),'conditional application cleanup refused')
    driver.core_unchanged()



def setup_hook_snapshot(driver):
    """Bind the retained upstream hook before uninstall; never infer from a pod name."""
    text=driver.command(['helm','--kube-context','k3d-uds','get','hooks','takserver','-n','takserver'])
    require(len(text.encode())<=256*1024,'upstream hook input bound')
    objects=[obj for obj in yaml.safe_load_all(text) if obj is not None]
    # Server 5.7-RELEASE-14, 5187abd46d827d37cfc5708805eced197a837e49,
    # templates/post-install-hook-postgres.yaml is the only upstream hook.
    if not objects:
        require(not driver.get('jobs',namespace='takserver')['items'],'unexpected application Job without stored hook')
        return None
    require(len(objects)==1,'upstream hook inventory differs')
    hook=objects[0];meta=hook.get('metadata',{});annotations=meta.get('annotations',{})
    require(hook.get('apiVersion')=='batch/v1' and hook.get('kind')=='Job' and
            meta.get('name')=='takserver-db-setup' and meta.get('namespace') in (None,'takserver') and
            annotations=={'helm.sh/hook':'post-install','helm.sh/hook-weight':'-5',
                         'helm.sh/hook-delete-policy':'before-hook-creation'} and
            meta.get('labels',{}).get('app.kubernetes.io/instance')=='takserver' and
            meta.get('labels',{}).get('app.kubernetes.io/managed-by')=='Helm', 'upstream setup hook ownership differs')
    job=driver.get('job','takserver-db-setup',namespace='takserver')
    validate_setup_hook(job)
    # Defaults and admission additions are allowed, but every stored field must
    # survive unchanged. Container inventories cannot be extended.
    def contains(actual,expected):
        if isinstance(expected,dict):return isinstance(actual,dict) and all(k in actual and contains(actual[k],v) for k,v in expected.items())
        if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(contains(a,b) for a,b in zip(actual,expected))
        return actual==expected
    require(contains(job['spec'],hook['spec']) and contains(job['metadata'],meta),'live setup hook differs from stored release')
    return {'record':resource_record(job),'specSHA256':sha(job['spec']),
            'storedHookSHA256':sha(hook)}


def validate_setup_hook(job):
    meta=job.get('metadata',{});status=job.get('status',{})
    require(job.get('apiVersion')=='batch/v1' and job.get('kind')=='Job' and
            meta.get('namespace')=='takserver' and meta.get('name')=='takserver-db-setup' and
            not meta.get('ownerReferences') and not meta.get('deletionTimestamp') and
            type(status.get('succeeded')) is int and status.get('succeeded')==1 and not status.get('active',0) and not status.get('failed',0) and
            any(c.get('type')=='Complete' and c.get('status')=='True' for c in status.get('conditions',[])),
            'setup hook is not an owned completed Job')


def cleanup_setup_hook(driver,proof):
    if proof is None:return
    driver.guard();driver.core_unchanged()
    rec=proof['record']
    require(rec['kind']=='Job' and rec['namespace']=='takserver' and rec['name']=='takserver-db-setup', 'setup cleanup scope differs')
    raw=driver.kube('get','job',rec['name'],'-n','takserver','--ignore-not-found','-o','json')
    if not raw.strip():return
    job=json.loads(raw);validate_setup_hook(job)
    require(resource_record(job)==rec and sha(job['spec'])==proof['specSHA256'], 'setup hook identity/spec changed after uninstall')
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        require(not driver.get('deployments',namespace=namespace)['items'] and
                not driver.get('statefulsets',namespace=namespace)['items'],'application workloads remain before hook cleanup')
        for pod in driver.get('pods',namespace=namespace)['items']:
            owners=pod['metadata'].get('ownerReferences',[])
            require(namespace=='takserver' and pod.get('status',{}).get('phase')=='Succeeded' and
                    len(owners)==1 and owners[0].get('apiVersion')=='batch/v1' and
                    owners[0].get('kind')=='Job' and owners[0].get('name')==rec['name'] and
                    owners[0].get('uid')==rec['uid'] and owners[0].get('controller') is True,
                    'foreign or live consumer before setup hook cleanup')
    # Foreground GC removes only this exact Job's children. All ordinary
    # consumer checks and retained-data cleanup remain unchanged.
    delete_record(driver,rec)
    driver.wait(lambda:consumers_gone(driver),300)
    driver.journal('completed-setup-hook-conditionally-removed',uid=rec['uid'])


def startup_map(obj):
    require(obj['kind']=='ConfigMap' and obj['metadata'].get('namespace')=='takserver' and obj['metadata']['name']=='takserver-startup' and obj['metadata'].get('annotations',{}).get('uds.dev/tak-startup')=='complete' and obj.get('data')=={'ready':'true'} and not obj.get('binaryData'),'startup map is not the literal source-generated checkpoint')


def generated_secret(driver,obj,cleanup=False):
    meta=obj['metadata'];name=meta['name'];namespace=meta['namespace']
    if namespace=='takserver' and name=='takserver-crl':
        if cleanup:no_live_clients(driver)
        config=driver.get('configmap','cert-migration')
        binary=config.get('binaryData',{})
        ca=(base64.b64decode(binary['ca.pem'],validate=True).decode() if 'ca.pem' in binary else config.get('data',{}).get('ca.pem'))
        empty_crl(obj,ca)
        return True
    if namespace=='cloudtak' and name=='cloudtak-tak-admin':
        require(meta.get('labels',{}).get('app.kubernetes.io/part-of')=='cloudtak','admin copy source label differs')
        config=driver.get('configmap','cert-migration');keystore=driver.get('secret','takserver-keystore')
        raw=driver.kube('get','secret','takserver-certs','-n','takserver','--ignore-not-found','-o','json')
        certificates=json.loads(raw).get('data',{}) if raw.strip() else {}
        binary=config.get('binaryData',{})
        expected={'ca.pem':binary.get('ca.pem') or certificates.get('ca.pem'),'admin.pem':binary.get('admin.pem') or certificates.get('admin.pem'),
                  'admin.key':certificates.get('admin.key') or binary.get('admin.key'),'CAPASS':keystore.get('data',{}).get('CAPASS')}
        require(all(expected.values()) and obj.get('data')==expected,'admin copy does not match exact source credentials')
        return True
    if namespace=='takserver' and name=='takclient-enrollment':
        require(meta.get('labels',{}).get('app.kubernetes.io/managed-by')=='takserver-oidc','enrollment cache source label differs')
        if cleanup:require(not driver.get('takclients.tak.jonathanagustin.github.io',namespace='takclients')['items'],'active client enrollment credentials must not be retired')
        return True
    return False


def ignite_jar(obj):
    """The literal publisher payload, bound separately to the application image."""
    meta=obj.get('metadata',{})
    require(obj.get('apiVersion')=='v1' and obj.get('kind')=='ConfigMap' and
            meta.get('namespace')=='takserver' and meta.get('name')=='ignite-extensions' and
            not meta.get('deletionTimestamp') and not meta.get('ownerReferences') and
            not obj.get('data') and set(obj.get('binaryData',{}))=={'ignite-extensions.jar'},
            'Ignite map differs from literal publisher payload')
    raw=base64.b64decode(obj['binaryData']['ignite-extensions.jar'],validate=True)
    require(0<len(raw)<=1024*1024,'Ignite binary size differs')
    return hashlib.sha256(raw).hexdigest()


def zarf_image_reference(registry,image):
    """Zarf v0.89.0 transform for fully-qualified TAK tag/digest references."""
    authority=r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?'
    require(isinstance(registry,str) and re.fullmatch(authority,registry),'unsupported Zarf registry authority')
    require(isinstance(image,str) and len(image)<=1024 and image.count('@')<=1,'unsupported source image')
    name_tag,_,digest=image.partition('@')
    slash=name_tag.rfind('/');colon=name_tag.rfind(':')
    name,tag=(name_tag[:colon],name_tag[colon+1:]) if colon>slash else (name_tag,'latest')
    host,separator,path=name.partition('/')
    require(separator and re.fullmatch(authority,host) and ('.' in host or ':' in host or host=='localhost') and
            host not in ('docker.io','index.docker.io') and
            re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*',path) and
            re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',tag) and
            (not digest or re.fullmatch(r'sha256:[a-f0-9]{64}',digest)), 'unsupported source image')
    if registry.startswith(host):return image
    if digest:return registry+'/'+path+'@'+digest
    return registry+'/'+path+':'+tag+'-zarf-'+str(zlib.crc32(name.encode()))


def zarf_image_binding(driver,pod,main,pod_image):
    # hooks/pods.go records the original image and marks its mutation. The
    # registry credential is generated from the same retained Zarf authority.
    key='zarf.dev/'+('original-image-'+main['name'])[:63].rstrip('-')
    meta=pod['metadata']
    require(meta.get('labels',{}).get('zarf-agent')=='patched' and
            meta.get('annotations',{}).get(key)==main['image'] and
            isinstance(pod_image,str) and '/' in pod_image,'Pod image has no exact Zarf source marker')
    source=driver.get('secret','private-registry',namespace='zarf')
    source_hash=registry_secret_digest(source,'zarf')
    raw=base64.b64decode(source['data']['.dockerconfigjson'],validate=True)
    require(len(raw)<=64*1024,'Zarf registry credential bound differs')
    def unique(pairs):
        value={}
        for k,v in pairs:
            require(k not in value,'duplicate Zarf registry field');value[k]=v
        return value
    config=json.loads(raw,object_pairs_hook=unique);auths=config.get('auths')
    registry=pod_image.split('/',1)[0]
    require(set(config)=={'auths'} and isinstance(auths,dict) and 1<=len(auths)<=64 and
            registry in auths and pod_image==zarf_image_reference(registry,main['image']),
            'Pod image differs from the bound Zarf registry transform')
    return source_hash


def ignite_image(driver):
    """Resolve one Ready config container through the frozen Deployment UID."""
    deployment=driver.get('deployment','takserver-config',namespace='takserver')
    meta=deployment['metadata'];uid=driver.state['deploymentUIDs']['takserver-config']
    require(meta.get('uid')==uid and not meta.get('deletionTimestamp'),'Ignite source Deployment changed')
    containers=deployment['spec']['template']['spec']['containers']
    require(containers and containers[0].get('name') and containers[0].get('image'),'Ignite source container missing')
    main=containers[0]
    replicasets={rs['metadata']['uid'] for rs in driver.get('replicasets',namespace='takserver')['items']
        if not rs['metadata'].get('deletionTimestamp') and
        any(o.get('kind')=='Deployment' and o.get('uid')==uid and o.get('controller') is True for o in rs['metadata'].get('ownerReferences',[]))}
    candidates=[]
    for pod in driver.get('pods',namespace='takserver')['items']:
        owners=[o for o in pod['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
        if len(owners)!=1 or owners[0].get('kind')!='ReplicaSet' or owners[0].get('uid') not in replicasets:continue
        if pod['metadata'].get('deletionTimestamp') or pod.get('status',{}).get('phase')!='Running':continue
        if not any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod['status'].get('conditions',[])):continue
        matches=[c for c in pod['spec']['containers'] if c['name']==main['name']]
        states=[c for c in pod['status'].get('containerStatuses',[]) if c['name']==main['name'] and c.get('ready') is True and c.get('state',{}).get('running')]
        if len(matches)!=1 or len(states)!=1:continue
        pod_image=matches[0].get('image')
        registry_source=(zarf_image_binding(driver,pod,main,pod_image) if pod_image!=main['image'] else None)
        require(not any(m.get('mountPath','').rstrip('/') in ('','/ignite-extensions.jar') for m in matches[0].get('volumeMounts',[])),'Ignite source file is volume-mounted')
        image_id=states[0].get('imageID','')
        require(bool(re.search(r'(?:^|@|://)sha256:[a-f0-9]{64}$',image_id)),'Ignite image has no immutable runtime identity')
        candidates.append({'pod':pod['metadata']['name'],'podUID':pod['metadata']['uid'],'ownerUID':owners[0]['uid'],
            'container':main['name'],'deploymentUID':uid,'image':main['image'],'imageID':image_id,
            'podImage':pod_image,'registrySourceSHA256':registry_source})
    require(len(candidates)==1,'Ignite source config Pod is not uniquely Ready')
    return candidates[0]


def ignite_binding(driver,obj):
    jar=ignite_jar(obj);before=ignite_image(driver)
    result=driver.exec(before['pod'],before['container'],['sha256sum','--','/ignite-extensions.jar'],namespace='takserver')
    require(result.returncode==0 and not result.stderr.strip() and
            result.stdout.strip()==jar+'  /ignite-extensions.jar','Ignite map differs from bound config image binary')
    require(ignite_image(driver)==before,'Ignite source changed during binary observation')
    return {'imageBoundJarSHA256':jar,'imageSourceSHA256':sha({k:before[k] for k in ('deploymentUID','image','imageID','container','podImage','registrySourceSHA256')})}


def trust_binding(driver,obj):
    """Bind public trust data to retained Core authority and exact source metadata."""
    meta=obj['metadata'];namespace=meta['namespace'];name=meta['name']
    require(namespace in APP_RELEASES and not obj.get('binaryData'),'trust map namespace/payload differs')
    if name=='kube-root-ca.crt':
        source_namespace,source_name,key,source_key='kube-system',name,'ca.crt','ca.crt'
        source_owner='kubernetes-root-ca-publisher'
    elif name in ('istio-ca-root-cert','istio-ca-crl'):
        require(meta.get('labels',{}).get('istio.io/config')=='true','Istio trust source marker missing')
        key='ca-crl.pem' if name=='istio-ca-crl' else 'root-cert.pem'
        source_namespace,source_name,source_key='istio-system',name,key
        source_owner='istio-namespace-controller'
    elif name=='uds-trust-bundle':
        labels=meta.get('labels',{});require(labels.get('uds/package')==namespace and labels.get('uds/ca-bundle')=='true','UDS bundle source labels differ')
        package=driver.get('package',namespace,namespace=namespace)
        owners=meta.get('ownerReferences',[])
        require(any(x.get('kind')=='Package' and x.get('uid')==package['metadata']['uid'] for x in owners),'UDS bundle has no exact Package owner')
        source_namespace,source_name,key,source_key='istio-system',name,'ca-bundle.pem','extra.pem'
        source_owner='uds-core-ca-bundle-controller'
    else:raise ValueError('unknown trust map')
    source=driver.get('configmap',source_name,namespace=source_namespace)
    require(set(obj.get('data',{}))=={key} and isinstance(obj['data'][key],str) and (bool(obj['data'][key]) or name=='istio-ca-crl') and source_key in source.get('data',{}) and obj['data'][key]==source['data'][source_key],'trust map is not the bound Core authority data')
    rec=resource_record(obj)
    rec.update(dataSHA256=sha(obj['data']),sourceOwner=source_owner,sourceNamespace=source_namespace,sourceName=source_name,sourceUID=source['metadata']['uid'],sourceDataSHA256=sha(source.get('data',{})))
    return rec


def capture_trust(driver):
    result=[]
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        for obj in driver.get('configmaps',namespace=namespace)['items']:
            if obj['metadata']['name'] in ('kube-root-ca.crt','istio-ca-root-cert','istio-ca-crl','uds-trust-bundle'):result.append(trust_binding(driver,obj))
    return result


def preserved_trust(driver,obj,bindings):
    matches=[r for r in bindings if r['namespace']==obj['metadata'].get('namespace') and r['name']==obj['metadata']['name']]
    require(len(matches)==1,'trust map has no bound source authority')
    rec=matches[0];require({k:v for k,v in resource_record(obj).items() if k!='resourceVersion'}=={k:rec[k] for k in ('kind','name','namespace','uid','metadataSHA256')} and sha(obj.get('data',{}))==rec['dataSHA256'],'bound trust map changed before clean-state observation')
    source=driver.get('configmap',rec['sourceName'],namespace=rec['sourceNamespace'])
    require(source['metadata']['uid']==rec['sourceUID'] and sha(source.get('data',{}))==rec['sourceDataSHA256'],'retained Core trust authority changed')
    return True


# Zarf v0.89.0 (embedded by UDS CLI v0.41.0) generates these namespace
# copies in src/internal/packager/helm/post-render.go using
# src/pkg/cluster/secrets.go:GenerateRegistryPullCreds. Never delete them.
def registry_secret_digest(obj,namespace):
    meta=obj.get('metadata',{})
    require(obj.get('apiVersion')=='v1' and obj.get('kind')=='Secret' and
            meta.get('namespace')==namespace and meta.get('name')=='private-registry' and
            meta.get('uid') and not meta.get('deletionTimestamp') and
            meta.get('labels',{}).get('app.kubernetes.io/managed-by')=='zarf' and
            obj.get('type')=='kubernetes.io/dockerconfigjson' and
            set(obj.get('data',{}))=={'.dockerconfigjson'} and not obj.get('stringData'),
            'registry credential is not the bound Zarf source shape')
    # Hash the whole object except API observation bookkeeping, including any
    # otherwise unfamiliar fields. No payload or credentials enter public proofs.
    value=copy.deepcopy(obj)
    for key in ('resourceVersion','managedFields'):value['metadata'].pop(key,None)
    return sha(value)


def registry_binding(driver,obj):
    namespace=obj.get('metadata',{}).get('namespace')
    require(namespace in APP_RELEASES,'registry copy outside application')
    target_hash=registry_secret_digest(obj,namespace)
    source=driver.get('secret','private-registry',namespace='zarf')
    source_hash=registry_secret_digest(source,'zarf')
    require(obj['data']==source['data'],'registry copy differs from retained Zarf credentials')
    return {'namespace':namespace,'name':'private-registry','objectSHA256':target_hash,
            'sourceObjectSHA256':source_hash}


def capture_registry(driver):
    result=[]
    for namespace in APP_RELEASES:
        if not driver.kube('get','namespace',namespace,'--ignore-not-found','-o','json').strip():continue
        for obj in driver.get('secrets',namespace=namespace)['items']:
            if obj['metadata']['name']=='private-registry':result.append(registry_binding(driver,obj))
    return result


def preserved_registry(driver,obj,bindings):
    namespace=obj.get('metadata',{}).get('namespace')
    matches=[r for r in bindings if r['namespace']==namespace and r['name']==obj['metadata']['name']]
    require(len(matches)==1,'registry copy has no bound Zarf authority')
    rec=registry_binding(driver,obj)
    require(rec==matches[0],'bound registry copy or retained Zarf authority changed')
    return True


def no_live_clients(driver):
    try:clients=driver.get('takclients.tak.jonathanagustin.github.io',namespace='takclients')['items']
    except ValueError:
        # The Pepr chart owns the CRD as a template, so uninstall may already
        # have removed the API. A failed list alone never establishes absence.
        require(not driver.kube('get','customresourcedefinition','takclients.tak.jonathanagustin.github.io',
                                '--ignore-not-found','-o','json').strip(),'TakClient inventory unavailable')
        return
    require(isinstance(clients,list) and not clients,
            'active clients prevent application CA and CRL retirement')


def empty_crl(obj,ca):
    """Authorize only an empty source-format CRL signed by the bound app CA."""
    meta=obj.get('metadata',{});data=obj.get('data',{})
    require(obj.get('apiVersion')=='v1' and obj.get('kind')=='Secret' and obj.get('type')=='Opaque' and meta.get('namespace')=='takserver' and
            meta.get('name')=='takserver-crl' and not meta.get('deletionTimestamp') and
            meta.get('labels',{}).get('app.kubernetes.io/part-of')=='takserver' and
            meta.get('labels',{}).get('uds.dev/pod-reload')=='false' and
            set(data)=={'ca.crl','revoked.json','number','ca-fingerprint','reload-at'} and
            not obj.get('stringData') and isinstance(ca,str) and 0<len(ca)<=65536,
            'CRL is not the empty source-generated checkpoint')
    require(all(isinstance(value,str) and len(value)<=131072 for value in data.values()),'CRL data bound differs')
    try:decoded={key:base64.b64decode(value,validate=True) for key,value in data.items()}
    except (ValueError,TypeError):raise ValueError('CRL encoding differs') from None
    require(decoded['revoked.json']==b'[]' and decoded['reload-at']==b'' and
            re.fullmatch(rb'[1-9][0-9]{0,19}',decoded['number']) and
            0<len(decoded['ca.crl'])<=65536,'CRL contains revocation or pending reload state')
    try:
        fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(ca)).hexdigest().upper()
    except (ValueError,TypeError):raise ValueError('CRL CA material differs') from None
    require(decoded['ca-fingerprint']==':'.join(fingerprint[i:i+2] for i in range(0,64,2)).encode(),
            'CRL issuer does not match application CA')
    with tempfile.TemporaryDirectory(prefix='owned-crl-check-') as directory:
        ca_path,crl_path=Path(directory)/'ca.pem',Path(directory)/'ca.crl'
        for path,value in ((ca_path,ca.encode()),(crl_path,decoded['ca.crl'])):
            path.write_bytes(value);path.chmod(0o600)
        try:
            verified=subprocess.run(['openssl','crl','-in',str(crl_path),'-CAfile',str(ca_path),'-verify','-noout'],
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            inspected=subprocess.run(['openssl','crl','-in',str(crl_path),'-noout','-text','-crlnumber'],
                                     stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10)
        except (OSError,subprocess.TimeoutExpired):raise ValueError('CRL verification unavailable') from None
    number=re.search(rb'^crlNumber=0x([0-9A-Fa-f]+)$',inspected.stdout,re.MULTILINE)
    require(verified.returncode==0 and inspected.returncode==0 and
            re.search(rb'^No Revoked Certificates\.$',inspected.stdout,re.MULTILINE) and
            b'Revoked Certificates:' not in inspected.stdout and number and
            int(number[1],16)==int(decoded['number']),'CRL signature, entries or number differs')
    return True
