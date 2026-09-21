import {defineConfig} from '@playwright/test';
import path from 'node:path';
const python=path.resolve('..','..','.envs','zhixing','python.exe');
export default defineConfig({testDir:'./e2e',workers:1,timeout:30000,use:{baseURL:'http://127.0.0.1:8000',headless:true,viewport:{width:1440,height:1000},screenshot:'only-on-failure'},webServer:{command:`"${python}" ../scripts/e2e_server.py`,url:'http://127.0.0.1:8000/api/health',reuseExistingServer:false,timeout:30000}});
