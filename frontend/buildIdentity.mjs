import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
const versionSource=readFileSync(new URL('../quality/versioning.py',import.meta.url),'utf8');
const release=versionSource.match(/^RELEASE = "([^"]+)"/m)?.[1];
const schema=versionSource.match(/^SCHEMA_HEAD = "([^"]+)"/m)?.[1];
if(!release||!schema)throw new Error('缺少后端规范版本，拒绝生成无法核对的前端');
export function qualityBuildIdentity(){return {name:'quality-build-identity',writeBundle(options,bundle){
 if(!options.dir)throw new Error('前端构建缺少输出目录');
 // Hash the final emitted bytes, after Vite has transformed the JS, CSS and HTML.
 const assets=Object.fromEntries(Object.keys(bundle).map(name=>[name,createHash('sha256').update(readFileSync(resolve(options.dir,name))).digest('hex')]));
 writeFileSync(resolve(options.dir,'build-info.json'),JSON.stringify({application_version:release,schema_required:schema,version_source:'quality/versioning.py',lock_sha256:createHash('sha256').update(readFileSync(new URL('./pnpm-lock.yaml',import.meta.url))).digest('hex'),assets},null,2));
 }};}
