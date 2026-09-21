const paths: Record<string,string> = {
  home:'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z',
  messages:'M3 5h18v14H3ZM3 6l9 7 9-7',
  todos:'m4 6 2 2 4-4M13 6h7M4 13l2 2 4-4M13 13h7M4 20h16',
  calendar:'M4 5h16v16H4ZM8 3v4M16 3v4M4 10h16M8 14h2M14 14h2M8 18h2',
  reminders:'M5 17h14l-2-3V9a5 5 0 0 0-10 0v5ZM10 21h4',
  runs:'M5 5h14v14H5ZM9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4M10 9l5 3-5 3Z',
  approvals:'m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6ZM8 12l3 3 5-6',
  drafts:'M5 3h10l4 4v14H5ZM14 3v5h5M8 12h8M8 16h6',
  skills:'m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3Z',
  parsers:'M8 4 2 12l6 8M16 4l6 8-6 8M14 3l-4 18',
  memory:'M12 3c-10 0-10 18 0 18s10-18 0-18ZM12 7c-5 0-5 10 0 10s5-10 0-10Z',
  trust:'M4 20V10M10 20V7M16 20V4M22 20V2M2 20h20',
  evaluations:'M4 3h16v18H4ZM8 8h8M8 12h3m3 4 2 2 4-5',
  audit:'M4 4h16M4 9h10M4 14h16M4 19h10',
  'model-calls':'M2 12h4l3-8 6 16 3-8h4',
  settings:'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6',
};
export function NavIcon({name}:{name:string}) {
  return <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]||paths.runs}/></svg>;
}
