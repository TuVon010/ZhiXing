import {defineConfig} from '@playwright/test';
import path from 'node:path';
const python=path.resolve('..','..','.envs','zhixing','python.exe');
const archive=process.env.ZHIXING_TEST_OUTPUT || path.resolve('../artifacts/browser',Date.now().toString());
export default defineConfig({testDir:'./e2e',workers:1,timeout:30000,
 outputDir:path.join(archive,'results'),
 reporter:[['list'],['html',{outputFolder:path.join(archive,'html'),open:'never'}],['json',{outputFile:path.join(archive,'results.json')}],['junit',{outputFile:path.join(archive,'junit.xml')}]],
 use:{baseURL:'http://127.0.0.1:8000',headless:true,viewport:{width:1440,height:1000},screenshot:'on',trace:'on',video:'on'},
 webServer:{command:`"${python}" ../scripts/e2e_server.py`,env:{ZHIXING_E2E_DATA_DIR:path.join(archive,'database')},url:'http://127.0.0.1:8000/api/health',reuseExistingServer:false,timeout:30000}});
