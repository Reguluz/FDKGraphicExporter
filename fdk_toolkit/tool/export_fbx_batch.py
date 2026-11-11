"""Run stable, resumable Blender partitions and optionally consume newly prepared glTF."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from script_paths import script_path, blender_script_args

def save_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, help='Path to Blender 4.5 blender.exe')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--tag',default='batch')
    parser.add_argument('--watch',action='store_true',help='Consume ready files until all_jobs.json exists')
    parser.add_argument('--ready-age',type=float,default=10)
    parser.add_argument('--exclude-audit',type=Path)
    args=parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    output=args.output.resolve()
    reports=args.output.resolve()/'reports'
    final_manifest=args.manifest or reports/'all_jobs.json'
    excluded=set()
    if args.exclude_audit:
        excluded={item['model'] for item in json.loads(args.exclude_audit.read_text(encoding='utf-8'))}
    script=script_path(Path(__file__).with_name('blender_export.py')).resolve()

    def partition(job):
        return int(hashlib.sha256(job['id'].encode()).hexdigest()[:16],16)%args.workers

    def ready_jobs():
        cutoff=time.time()-args.ready_age
        ready=[]
        for gltf in sorted((output/'gltf').glob('*.gltf')):
            if gltf.name.startswith('.') or gltf.stem in excluded:
                continue
            companions=[gltf,gltf.with_suffix('.bin'),gltf.with_suffix('.metadata')]
            if all(p.is_file() and p.stat().st_mtime<cutoff for p in companions):
                ready.append({'id':gltf.stem,'gltf':str(gltf),'fbx':str(output/'fbx'/(gltf.stem+'.fbx'))})
        return ready

    def run_batch(index):
        tag=f'{args.tag}_{index:02d}'
        manifest=reports/f'{tag}_jobs.json'
        report=reports/f'{tag}_fbx.json'
        assigned=[job for job in jobs if partition(job)==index]
        save_json(manifest,{'jobs':assigned})
        command=[args.blender,'--background','--factory-startup','--threads','2',*blender_script_args(script),'--',
                 '--manifest',str(manifest),'--report',str(report),'--verify','--resume']
        with (reports/f'{tag}.log').open('a',encoding='utf-8') as log:
            log.write(f'\n--- Round {time.strftime("%Y-%m-%d %H:%M:%S")} requested={len(assigned)} ---\n')
            log.flush()
            process=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            exit_code=process.returncode
            if report.is_file():
                original=json.loads(report.read_text(encoding='utf-8'))
                nested_ids={j['id'] for j in original['jobs'] if j.get('status')=='error'
                            and 'KeyError:' in j.get('error','') and 'armature_setup' in j.get('error','')}
                if nested_ids:
                    repair_manifest=reports/f'{tag}_rig_jobs.json'
                    repair_report=reports/f'{tag}_rig_repair.json'
                    save_json(repair_manifest,{'jobs':[j for j in assigned if j['id'] in nested_ids]})
                    repair_command=[args.blender,'--background','--factory-startup','--threads','2',
                                    *blender_script_args(script.with_name('blender_rig_repair.py')),'--',
                                    '--manifest',str(repair_manifest),'--report',str(repair_report),'--verify']
                    repair_process=subprocess.run(repair_command,stdout=log,stderr=subprocess.STDOUT,
                                                  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    if repair_report.is_file():
                        combined={j['id']:j for j in original['jobs']}
                        for item in json.loads(repair_report.read_text(encoding='utf-8'))['jobs']:
                            combined[item['id']]=item
                        original['jobs']=sorted(combined.values(),key=lambda j:j['id'])
                        original['completed']=sum(j['status']=='ok' for j in original['jobs'])
                        original['failed']=sum(j['status']!='ok' for j in original['jobs'])
                        save_json(report,original)
                        exit_code=0 if original['failed']==0 and repair_process.returncode==0 else 1
        return {'batch':index,'exit_code':exit_code,'report':str(report)}
    prior_ready=None
    while True:
        final_available=final_manifest.is_file()
        if final_available:
            manifest_data=json.loads(final_manifest.read_text(encoding='utf-8'))
            all_jobs=manifest_data['jobs'] if isinstance(manifest_data,dict) else manifest_data
            jobs=[job for job in all_jobs if job['id'] not in excluded]
        elif args.watch:
            jobs=ready_jobs()
        else:
            raise FileNotFoundError(final_manifest)
        signature=[(job['id'],Path(job['gltf']).stat().st_mtime_ns) for job in jobs]
        if not final_available and (not jobs or signature==prior_ready):
            time.sleep(5)
            continue
        print(f'KURO_BATCH ready={len(jobs)} final_manifest={final_available}',flush=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            batches=list(pool.map(run_batch,range(args.workers)))
        results=[]
        for batch in batches:
            if Path(batch['report']).exists():
                results.extend(json.loads(Path(batch['report']).read_text(encoding='utf-8'))['jobs'])
        result_by_id={j['id']:j for j in results}
        summary={'requested':len(jobs),'excluded_ids':sorted(excluded),
                 'completed':sum(j['status']=='ok' for j in result_by_id.values()),
                 'failed':[j for j in result_by_id.values() if j['status']!='ok'],
                 'unprocessed':[j['id'] for j in jobs if j['id'] not in result_by_id],
                 'batches':batches,'jobs':sorted(result_by_id.values(),key=lambda x:x['id'])}
        save_json(reports/f'{args.tag}_summary.json',summary)
        print(json.dumps({'requested':summary['requested'],'completed':summary['completed'],
                          'failed':[{'id':j['id'],'error':j.get('error')} for j in summary['failed']],
                          'unprocessed':summary['unprocessed'],'excluded':len(excluded),
                          'batch_exit_codes':[b['exit_code'] for b in batches]},ensure_ascii=False),flush=True)
        prior_ready=signature
        if final_available:
            if summary['failed'] or summary['unprocessed'] or any(b['exit_code'] for b in batches):sys.exit(1)
            break
        time.sleep(2)

if __name__=='__main__':main()
