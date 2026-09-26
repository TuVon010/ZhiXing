import React from 'react';
import { createRoot } from 'react-dom/client';
import { MailApp } from './app/MailApp';
import { api } from './shared/api/client';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><MailApp api={api} /></React.StrictMode>,
);
