import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import {qualityBuildIdentity} from './buildIdentity.mjs';
export default defineConfig({ plugins:[react(),qualityBuildIdentity()], server:{proxy:{'/api':'http://127.0.0.1:8000','/metrics':'http://127.0.0.1:8000'}}, build:{chunkSizeWarningLimit:1400} });
