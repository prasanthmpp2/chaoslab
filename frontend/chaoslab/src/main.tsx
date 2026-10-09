import React from 'react';import {createRoot} from 'react-dom/client';import {QueryClient,QueryClientProvider} from '@tanstack/react-query';import {HashRouter} from 'react-router-dom';
import App from './App';import './styles.css';
const qc=new QueryClient({defaultOptions:{queries:{retry:1,refetchOnWindowFocus:true,staleTime:5000}}});
createRoot(document.getElementById('root')!).render(<React.StrictMode><QueryClientProvider client={qc}><HashRouter><App/></HashRouter></QueryClientProvider></React.StrictMode>);
