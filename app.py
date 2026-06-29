<!-- Painel O&M v3.3 — 2026-06-27 — NÃO REMOVA ESTE COMENTÁRIO -->
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0E1419" id="themeColorMeta">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="O&M Painel">
<meta name="description" content="Painel de Operação e Manutenção — Acompanhamento de Falhas">
<link rel="manifest" href="#" id="manifestLink">
<title>Painel de Falhas — O&M</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  /* Geist via CDN — fonte oficial da Vercel */
  @font-face { font-family:'Geist'; font-weight:400; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-sans/Geist-Regular.woff2') format('woff2'); }
  @font-face { font-family:'Geist'; font-weight:500; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-sans/Geist-Medium.woff2') format('woff2'); }
  @font-face { font-family:'Geist'; font-weight:600; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-sans/Geist-SemiBold.woff2') format('woff2'); }
  @font-face { font-family:'Geist'; font-weight:700; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-sans/Geist-Bold.woff2') format('woff2'); }
  @font-face { font-family:'Geist Mono'; font-weight:400; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-mono/GeistMono-Regular.woff2') format('woff2'); }
  @font-face { font-family:'Geist Mono'; font-weight:500; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-mono/GeistMono-Medium.woff2') format('woff2'); }
  @font-face { font-family:'Geist Mono'; font-weight:600; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-mono/GeistMono-SemiBold.woff2') format('woff2'); }
  @font-face { font-family:'Geist Mono'; font-weight:700; font-style:normal; font-display:swap;
    src: url('https://cdn.jsdelivr.net/npm/geist@1.3.0/dist/fonts/geist-mono/GeistMono-Bold.woff2') format('woff2'); }
</style>
<style>
/* ══════════════════════════════════════════
   TOKENS — DARK (padrão)
══════════════════════════════════════════ */
:root {
  --bg:#0E1419; --bg-grid:rgba(255,255,255,0.022);
  --panel:#161E26; --panel-alt:#1B2430;
  --border:#253040; --border-soft:#1E2A36;
  --text:#E8ECF0; --text-dim:#8FA0AE; --text-faint:#536070;
  --amber:#F2A93B; --teal:#3FC1B0; --red:#E2543D; --blue:#5B9BD5; --gray:#6B7780;
  --radius:10px; --shadow:0 10px 30px rgba(0,0,0,.45);
  --sla-ok:#22C55E; --sla-warn:#EAB308; --sla-breach:#E2543D;
  --toggle-bg:#253040; --toggle-thumb:#8FA0AE;
}
[data-theme="light"] {
  --bg:#F0F4F8; --bg-grid:rgba(0,0,0,0.04);
  --panel:#FFFFFF; --panel-alt:#F5F8FB;
  --border:#D1DCE8; --border-soft:#E2EAF2;
  --text:#1A2533; --text-dim:#4A6070; --text-faint:#8FA0AE;
  --shadow:0 4px 20px rgba(0,0,0,.10);
  --toggle-bg:#C8D8E8; --toggle-thumb:#FFFFFF;
}
[data-theme="light"] body {
  background:
    linear-gradient(var(--bg-grid) 1px,transparent 1px) 0 0/100% 28px,
    linear-gradient(90deg,var(--bg-grid) 1px,transparent 1px) 0 0/28px 100%,
    var(--bg);
}
[data-theme="light"] .kpi::after { opacity:.5; }
[data-theme="light"] .login-box { box-shadow:0 8px 40px rgba(0,0,0,.15); }
*{box-sizing:border-box;margin:0;padding:0;}
html,body{min-height:100vh;}
body{
  background:
    linear-gradient(var(--bg-grid) 1px,transparent 1px) 0 0/100% 28px,
    linear-gradient(90deg,var(--bg-grid) 1px,transparent 1px) 0 0/28px 100%,
    var(--bg);
  color:var(--text); font-family:'Geist',sans-serif; -webkit-font-smoothing:antialiased;
  transition: background .3s ease, color .2s ease;
}
.wrap{width:100%;max-width:100%;margin:0 auto;padding:28px clamp(16px,3vw,48px) 72px;}
.theme-toggle {
  display:flex; align-items:center; gap:8px;
  background:var(--toggle-bg); border:1px solid var(--border);
  border-radius:99px; padding:4px 10px 4px 6px;
  cursor:pointer; user-select:none; transition:.2s;
}
.theme-toggle:hover { border-color:var(--amber); }
.theme-toggle-track {
  width:32px; height:18px; border-radius:99px;
  background:var(--panel-alt); border:1px solid var(--border);
  position:relative; transition:.25s; flex:none;
}
.theme-toggle-thumb {
  width:12px; height:12px; border-radius:50%;
  background:var(--amber); position:absolute;
  top:2px; left:2px; transition:.25s cubic-bezier(.4,0,.2,1);
}
[data-theme="light"] .theme-toggle-thumb { left:16px; background:var(--amber); }
.theme-toggle-label { font-size:11px; font-family:'Geist Mono',monospace; color:var(--text-faint); white-space:nowrap; }
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:28px;padding-bottom:20px;border-bottom:1px solid var(--border-soft);}
.eyebrow{font-family:'Geist Mono',monospace;font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--amber);margin-bottom:8px;font-weight:600;}
h1{font-size:26px;font-weight:700;letter-spacing:-.015em;line-height:1.15;}
.sub{color:var(--text-dim);font-size:13px;margin-top:6px;}
.header-right{display:flex;flex-direction:column;align-items:flex-end;gap:8px;}
.source-badge{
  display:flex; align-items:center; gap:8px;
  font-family:'Geist Mono',monospace; font-size:11px; color:var(--text-faint);
  background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:6px 12px;
}
.source-dot{width:7px;height:7px;border-radius:50%;background:var(--gray);flex:none;transition:.3s;}
.source-badge.live .source-dot{background:var(--teal);animation:dotpulse 2s infinite;}
.source-badge.error .source-dot{background:var(--red);}
.source-badge.loading .source-dot{background:var(--amber);animation:dotpulse 1s infinite;}
@keyframes dotpulse{0%,100%{opacity:1;}50%{opacity:.3;}}
.countdown-wrap{
  display:flex; align-items:center; gap:8px;
  background:var(--panel); border:1px solid var(--border);
  border-radius:8px; padding:6px 12px;
  font-family:'Geist Mono',monospace; font-size:11px; color:var(--text-faint);
}
.countdown-bar-track{ width:80px; height:4px; background:var(--panel-alt); border-radius:99px; overflow:hidden; }
.countdown-bar-fill{ height:100%; border-radius:99px; background:var(--teal); transition:width 1s linear, background .5s; }
#countdownText { color:var(--text-dim); font-weight:600; }
.update-btn{
  display:inline-flex;align-items:center;gap:8px;
  background:rgba(242,169,59,.1);border:1px solid rgba(242,169,59,.35);color:var(--amber);
  border-radius:8px;padding:9px 16px;font-size:12px;font-weight:600;cursor:pointer;
  font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;
}
.update-btn:hover:not(:disabled){background:rgba(242,169,59,.18);border-color:var(--amber);}
.update-btn:disabled{opacity:.5;cursor:not-allowed;}
.update-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2.2;flex:none;transition:transform .6s;}
.update-btn.spinning svg{animation:spin .7s linear infinite;}
@keyframes spin{to{transform:rotate(360deg);}}
.total-badge{font-family:'Geist Mono',monospace;font-size:11px;color:var(--text-faint);text-align:right;}
.toast{
  position:fixed;bottom:24px;right:24px;z-index:999;
  background:var(--panel);border-radius:10px;padding:12px 18px;
  font-size:13px;font-family:'Geist Mono',monospace;box-shadow:var(--shadow);
  opacity:0;transform:translateY(8px);transition:.3s ease;pointer-events:none;
  display:flex;align-items:center;gap:10px;border:1px solid var(--teal);color:var(--teal);
}
.toast.show{opacity:1;transform:none;}
.toast.err{border-color:var(--red);color:var(--red);}
.toast svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2.2;flex:none;}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px;}
.kpi{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:18px 20px;position:relative;overflow:hidden;}
.kpi::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--kpi-color,var(--amber));}
.kpi::after{content:"";position:absolute;inset:0;background:radial-gradient(ellipse at 0% 50%,var(--kpi-glow,rgba(242,169,59,.06)) 0%,transparent 70%);pointer-events:none;}
.kpi .num{font-family:'Geist Mono',monospace;font-size:34px;font-weight:700;line-height:1;}
.kpi .label{font-size:12px;color:var(--text-dim);margin-top:8px;line-height:1.3;}
.kpi .kicon{position:absolute;right:14px;top:50%;transform:translateY(-50%);opacity:.07;}
.kpi .kicon svg{width:44px;height:44px;stroke:var(--kpi-color,var(--amber));fill:none;stroke-width:1.4;}
.sla-banner {
  display:none; align-items:center; gap:10px;
  padding:10px 16px; margin-bottom:16px;
  background:rgba(226,84,61,.08); border:1px solid rgba(226,84,61,.3);
  border-radius:var(--radius); font-size:13px;
  font-family:'Geist Mono',monospace; color:var(--red);
  animation: sla-pulse-border 2.5s infinite;
  cursor:pointer; user-select:none;
}
.sla-banner:hover { background:rgba(226,84,61,.14); }
.sla-banner.active-filter { background:rgba(226,84,61,.18); border-color:var(--red); }
.sla-banner.visible { display:flex; }
.sla-banner svg { width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:2;flex:none; }
@keyframes sla-pulse-border { 0%,100% { border-color:rgba(226,84,61,.3); } 50% { border-color:rgba(226,84,61,.7); } }
.filters{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;margin-bottom:20px;}
.filter-group{display:flex;flex-direction:column;gap:5px;min-width:148px;}
.filters label{font-size:10.5px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.07em;font-family:'Geist Mono',monospace;font-weight:600;}
select,input[type="text"]{background:var(--panel-alt);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:8px 10px;font-size:13px;font-family:'Geist',sans-serif;outline:none;transition:.15s;}
select:focus,input[type="text"]:focus{border-color:var(--amber);background:rgba(242,169,59,.04);}
.search-group{flex:1;min-width:200px;}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text-dim);border-radius:7px;padding:9px 14px;font-size:12px;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;white-space:nowrap;}
.btn-ghost:hover{border-color:var(--red);color:var(--red);}
.result-count{margin-left:auto;font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;align-self:flex-end;padding-bottom:2px;}
#loginScreen{
  position:fixed;inset:0;z-index:9999;
  background:linear-gradient(var(--bg-grid) 1px,transparent 1px) 0 0/100% 28px,linear-gradient(90deg,var(--bg-grid) 1px,transparent 1px) 0 0/28px 100%,var(--bg);
  display:flex;align-items:center;justify-content:center;padding:24px;
}
#loginScreen.hidden{display:none;}
.login-box{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:40px 40px 36px;width:100%;max-width:400px;box-shadow:0 24px 64px rgba(0,0,0,.6);animation:modalIn .25s cubic-bezier(.2,.8,.4,1);}
.login-logo{width:48px;height:48px;border-radius:12px;margin:0 auto 20px;background:linear-gradient(135deg,rgba(242,169,59,.2),rgba(91,155,213,.15));border:1px solid rgba(242,169,59,.3);display:flex;align-items:center;justify-content:center;}
.login-logo svg{width:24px;height:24px;stroke:var(--amber);fill:none;stroke-width:1.8;}
.login-title{font-size:20px;font-weight:700;text-align:center;margin-bottom:4px;}
.login-sub{font-size:12.5px;color:var(--text-dim);text-align:center;margin-bottom:28px;font-family:'Geist Mono',monospace;}
.login-field{margin-bottom:16px;}
.login-field label{display:block;font-size:10.5px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.08em;font-family:'Geist Mono',monospace;font-weight:600;margin-bottom:6px;}
.login-field input{width:100%;background:var(--panel-alt);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:11px 14px;font-size:14px;font-family:'Geist',sans-serif;outline:none;transition:.15s;}
.login-field input:focus{border-color:var(--amber);background:rgba(242,169,59,.04);}
.login-error{font-size:12px;color:var(--red);font-family:'Geist Mono',monospace;background:rgba(226,84,61,.08);border:1px solid rgba(226,84,61,.2);border-radius:7px;padding:8px 12px;margin-bottom:14px;display:none;align-items:center;gap:8px;}
.login-error.show{display:flex;}
.login-error svg{width:13px;height:13px;stroke:var(--red);fill:none;stroke-width:2;flex:none;}
.login-submit{width:100%;background:linear-gradient(135deg,rgba(242,169,59,.2),rgba(242,169,59,.12));border:1px solid rgba(242,169,59,.45);color:var(--amber);border-radius:8px;padding:12px;font-size:14px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;letter-spacing:.05em;transition:.2s;margin-top:4px;}
.login-submit:hover{background:rgba(242,169,59,.28);border-color:var(--amber);box-shadow:0 0 20px rgba(242,169,59,.2);}
.login-submit:active{transform:scale(.98);}
.login-footer{text-align:center;font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;margin-top:20px;}
.client-legend{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:20px;padding:10px 14px;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);}
.legend-label{font-size:10.5px;color:var(--text-faint);font-family:'Geist Mono',monospace;text-transform:uppercase;letter-spacing:.08em;margin-right:4px;}
.legend-item{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:6px;border:1px solid;font-size:12px;font-family:'Geist Mono',monospace;font-weight:600;letter-spacing:.03em;cursor:pointer;transition:.18s;user-select:none;}
.legend-item:hover{filter:brightness(1.2);}
.legend-item.selected{filter:brightness(1.15);box-shadow:0 0 0 2px currentColor;}
.legend-item.dimmed{opacity:.35;}
.legend-dot{width:8px;height:8px;border-radius:50%;flex:none;}
.chamados-btn{display:inline-flex;align-items:center;gap:8px;background:linear-gradient(135deg,rgba(91,155,213,.18),rgba(63,193,176,.12));border:1px solid rgba(91,155,213,.45);color:#7DC8F0;border-radius:8px;padding:9px 18px;font-size:13px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;letter-spacing:.04em;position:relative;overflow:hidden;}
.chamados-btn:hover{border-color:rgba(91,155,213,.8);color:#a8d8f8;box-shadow:0 0 16px rgba(91,155,213,.25);}
.chamados-btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.chamados-badge{background:rgba(91,155,213,.25);border:1px solid rgba(91,155,213,.4);border-radius:99px;font-size:10px;font-weight:700;padding:1px 7px;font-family:'Geist Mono',monospace;}
.ch-overlay{position:fixed;inset:0;background:rgba(8,12,18,.82);backdrop-filter:blur(4px);display:none;align-items:center;justify-content:center;z-index:150;padding:24px;}
.ch-overlay.open{display:flex;}
.ch-modal{background:var(--panel);border:1px solid var(--border);border-radius:14px;max-width:580px;width:100%;max-height:88vh;overflow-y:auto;box-shadow:0 24px 64px rgba(0,0,0,.65);border-top:4px solid #5B9BD5;animation:modalIn .2s cubic-bezier(.2,.8,.4,1);}
.ch-head{padding:20px 22px 14px;border-bottom:1px solid var(--border-soft);position:sticky;top:0;background:var(--panel);z-index:2;border-radius:10px 10px 0 0;}
.ch-head-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}
.ch-title{display:flex;align-items:center;gap:10px;}
.ch-title svg{width:18px;height:18px;stroke:#7DC8F0;fill:none;stroke-width:2;flex:none;}
.ch-title h2{font-size:18px;margin:0;color:var(--text);}
.ch-subtitle{font-size:12px;color:var(--text-faint);font-family:'Geist Mono',monospace;margin-top:5px;}
.ch-body{padding:16px 22px 24px;}
.ch-item{background:var(--panel-alt);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin-bottom:10px;display:flex;align-items:center;gap:16px;}
.ch-item:last-child{margin-bottom:0;}
.ch-item:hover{background:rgba(91,155,213,.06);}
.ch-ticket{font-family:'Geist Mono',monospace;font-size:15px;font-weight:700;color:#7DC8F0;min-width:90px;}
.ch-info{flex:1;}
.ch-equip{font-size:13.5px;font-weight:600;color:var(--text);margin-bottom:3px;}
.ch-meta{font-size:11.5px;color:var(--text-dim);font-family:'Geist Mono',monospace;}
.ch-status{flex:none;}
.ch-empty{text-align:center;padding:40px 20px;color:var(--text-faint);font-size:13px;}
.modal-close-btn{background:var(--panel-alt);border:1px solid var(--border);color:var(--text-faint);width:30px;height:30px;border-radius:8px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:none;transition:.15s;}
.modal-close-btn:hover{color:var(--text);}
.ms-wrap{position:relative;min-width:148px;}
.ms-label{font-size:10.5px;color:var(--text-faint);text-transform:uppercase;letter-spacing:.07em;font-family:'Geist Mono',monospace;font-weight:600;margin-bottom:5px;}
.ms-trigger{display:flex;align-items:center;justify-content:space-between;gap:8px;background:var(--panel-alt);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:8px 10px;font-size:13px;font-family:'Geist',sans-serif;cursor:pointer;user-select:none;transition:.15s;min-height:38px;width:100%;}
.ms-trigger:hover,.ms-trigger.open{border-color:var(--amber);background:rgba(242,169,59,.04);}
.ms-trigger-text{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:left;}
.ms-trigger-text.active{color:var(--amber);font-weight:500;}
.ms-arrow{width:12px;height:12px;stroke:var(--text-faint);fill:none;stroke-width:2;flex:none;transition:transform .2s;}
.ms-trigger.open .ms-arrow{transform:rotate(180deg);}
.ms-badge{background:var(--amber);color:#0E1419;border-radius:99px;font-size:10px;font-weight:700;padding:1px 6px;flex:none;font-family:'Geist Mono',monospace;}
.ms-dropdown{position:absolute;top:calc(100% + 6px);left:0;min-width:100%;max-width:260px;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 12px 32px rgba(0,0,0,.45);z-index:200;max-height:220px;overflow-y:auto;display:none;}
.ms-dropdown.open{display:block;animation:dropIn .15s ease;}
@keyframes dropIn{from{opacity:0;transform:translateY(-4px);}to{opacity:1;transform:none;}}
.ms-option{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;font-size:13px;color:var(--text);transition:.12s;border-bottom:1px solid var(--border-soft);}
.ms-option:last-child{border-bottom:none;}
.ms-option:hover{background:var(--panel-alt);}
.ms-option.checked{background:rgba(242,169,59,.05);}
.ms-option.all-opt{color:var(--text-dim);font-size:12px;border-bottom:1px solid var(--border);}
.ms-deselect-all{padding:7px 12px;font-size:11px;font-family:'Geist Mono',monospace;color:var(--red);cursor:pointer;border-bottom:1px solid var(--border);margin-bottom:2px;opacity:.8;}
.ms-deselect-all:hover{opacity:1;background:rgba(226,84,61,.08);}
.ms-check{width:15px;height:15px;border:1.5px solid var(--border);border-radius:4px;flex:none;display:flex;align-items:center;justify-content:center;transition:.12s;background:transparent;}
.ms-option.checked .ms-check{background:var(--amber);border-color:var(--amber);}
.ms-check svg{width:10px;height:10px;stroke:#0E1419;fill:none;stroke-width:2.5;opacity:0;}
.ms-option.checked .ms-check svg{opacity:1;}
.panels{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-bottom:26px;}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;}
.panel-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;}
.panel-head h3{font-size:10.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--text-faint);font-family:'Geist Mono',monospace;font-weight:600;}
.panel-total{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:10px;cursor:pointer;border-radius:7px;padding:5px 6px;transition:.15s;}
.bar-row:last-child{margin-bottom:0;}
.bar-row:hover{background:var(--panel-alt);}
.bar-row.selected{background:rgba(242,169,59,.06);outline:1px solid rgba(242,169,59,.3);}
.bar-label{font-size:12.5px;width:115px;flex:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text);}
.bar-track{flex:1;height:6px;background:var(--panel-alt);border-radius:4px;overflow:hidden;}
.bar-fill{height:100%;border-radius:4px;transition:width .4s ease;}
.bar-count{font-family:'Geist Mono',monospace;font-size:11.5px;color:var(--text-dim);width:22px;text-align:right;flex:none;}
.grid-controls{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:10px;}
.grid-title{display:flex;align-items:baseline;gap:10px;}
.grid-title h2{font-size:15px;color:var(--text);font-weight:600;}
.grid-title span{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;}
.sort-group{display:flex;gap:6px;align-items:center;}
.sort-label{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;}
.sort-btn{background:var(--panel);border:1px solid var(--border);color:var(--text-dim);border-radius:6px;padding:5px 10px;font-size:11px;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;}
.sort-btn.active{border-color:var(--amber);color:var(--amber);background:rgba(242,169,59,.07);}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(272px,1fr));gap:14px;}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:16px;cursor:pointer;transition:.2s ease;border-bottom:3px solid var(--card-color,var(--gray));display:flex;flex-direction:column;}
.card:hover{transform:translateY(-3px);border-color:var(--card-color,var(--border));box-shadow:var(--shadow),0 0 0 1px var(--card-color,transparent);}
.card:focus-visible{outline:2px solid var(--amber);outline-offset:2px;}
.card.sla-warning{border-color:var(--sla-warn)!important;box-shadow:0 0 0 1px rgba(234,179,8,.2);}
.card.sla-breach{border-color:var(--sla-breach)!important;animation:card-sla-pulse 2.5s infinite;}
@keyframes card-sla-pulse{0%,100%{box-shadow:0 0 0 0 rgba(226,84,61,0);}50%{box-shadow:0 0 0 4px rgba(226,84,61,.25);}}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:10px;}
.tag-row{display:flex;align-items:center;gap:8px;}
.icon-badge{width:34px;height:34px;border-radius:8px;background:var(--panel-alt);display:flex;align-items:center;justify-content:center;flex:none;border:1px solid var(--border-soft);}
.icon-badge svg{width:17px;height:17px;stroke:var(--card-color,var(--amber));fill:none;stroke-width:1.6;}
.id-badge{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--text-faint);}
.usina-name{font-size:11px;color:var(--text-dim);margin-top:2px;}
.status-led{width:9px;height:9px;border-radius:50%;background:var(--card-color,var(--gray));flex:none;margin-top:4px;}
.status-led.pulse{animation:ledpulse 2.4s infinite;}
@keyframes ledpulse{0%{box-shadow:0 0 0 0 var(--card-glow,rgba(226,84,61,.6));}70%{box-shadow:0 0 0 7px transparent;}100%{box-shadow:0 0 0 0 transparent;}}
.card .equip{font-size:14px;font-weight:600;margin:0 0 2px;color:var(--text);}
.card .falha{font-size:12.5px;color:var(--text-dim);line-height:1.5;margin-bottom:8px;flex:1;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.age-row{display:flex;align-items:center;gap:6px;margin-bottom:10px;}
.age-pill{display:inline-flex;align-items:center;gap:5px;font-family:'Geist Mono',monospace;font-size:11px;font-weight:600;padding:3px 9px;border-radius:99px;letter-spacing:.02em;}
.age-pill svg{width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:2.2;flex:none;}
.age-pill.age-ok{background:rgba(34,197,94,.1);color:var(--sla-ok);border:1px solid rgba(34,197,94,.25);}
.age-pill.age-warning{background:rgba(234,179,8,.1);color:var(--sla-warn);border:1px solid rgba(234,179,8,.25);}
.age-pill.age-breach{background:rgba(226,84,61,.12);color:var(--sla-breach);border:1px solid rgba(226,84,61,.3);}
.age-pill.age-none{background:var(--panel-alt);color:var(--text-faint);border:1px solid var(--border-soft);}
.sla-badge-card{display:none;font-family:'Geist Mono',monospace;font-size:10px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:99px;background:rgba(226,84,61,.15);color:var(--red);border:1px solid rgba(226,84,61,.35);}
.card.sla-breach .sla-badge-card{display:inline-block;}
.stale-pill{display:inline-flex;align-items:center;gap:5px;font-family:'Geist Mono',monospace;font-size:10.5px;font-weight:700;padding:3px 9px;border-radius:99px;letter-spacing:.02em;background:rgba(107,119,128,.12);color:#9BAAB8;border:1px solid rgba(107,119,128,.3);animation:stale-pulse 3s infinite;}
.stale-pill svg{width:11px;height:11px;stroke:currentColor;fill:none;stroke-width:2.2;flex:none;}
@keyframes stale-pulse{0%,100%{opacity:1;border-color:rgba(107,119,128,.3);}50%{opacity:.7;border-color:rgba(107,119,128,.6);}}
.card.card-stale{border-left-color:#536070!important;}
.card.sla-breach.card-stale{border-left-color:var(--sla-breach)!important;}
.fault-table tbody tr.tr-stale td:first-child{border-left:3px solid #536070;}
.td-stale-pill{display:inline-flex;align-items:center;gap:4px;font-family:'Geist Mono',monospace;font-size:10px;font-weight:700;padding:2px 7px;border-radius:99px;background:rgba(107,119,128,.1);color:#8FA0AE;border:1px solid rgba(107,119,128,.25);white-space:nowrap;}
.td-stale-pill svg{width:9px;height:9px;stroke:currentColor;fill:none;stroke-width:2.5;flex:none;}
.card-foot{display:flex;justify-content:space-between;align-items:center;border-top:1px solid var(--border-soft);padding-top:10px;margin-top:auto;}
.status-chip{font-size:10px;font-family:'Geist Mono',monospace;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:5px;color:var(--card-color,var(--text-dim));border:1px solid;}
.see-more{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;display:flex;align-items:center;gap:4px;}
.see-more svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;}
.card:hover .see-more{color:var(--amber);}
.empty-state{text-align:center;padding:64px 20px;color:var(--text-faint);border:1px dashed var(--border);border-radius:var(--radius);grid-column:1/-1;}
.empty-state strong{display:block;color:var(--text-dim);margin-bottom:8px;font-size:15px;}
.reset-link{margin-top:12px;font-size:12px;cursor:pointer;color:var(--amber);font-family:'Geist Mono',monospace;background:none;border:none;text-decoration:underline;}
.view-toggle{display:flex;gap:2px;background:var(--panel-alt);border:1px solid var(--border);border-radius:8px;padding:3px;}
.view-btn{display:flex;align-items:center;gap:6px;background:transparent;border:none;color:var(--text-faint);border-radius:6px;padding:6px 11px;font-size:11px;font-weight:600;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;white-space:nowrap;}
.view-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.view-btn.active{background:var(--panel);color:var(--amber);box-shadow:0 1px 4px rgba(0,0,0,.2);}
.view-btn:hover:not(.active){color:var(--text);}
.table-wrap{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;overflow-x:auto;}
.fault-table{width:100%;border-collapse:collapse;font-size:13px;min-width:860px;}
.fault-table thead tr{background:var(--panel-alt);border-bottom:2px solid var(--border);}
.fault-table th{padding:11px 14px;text-align:left;font-family:'Geist Mono',monospace;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);white-space:nowrap;user-select:none;}
.fault-table th.sortable{cursor:pointer;}
.fault-table th.sortable:hover{color:var(--text-dim);}
.fault-table th.th-sorted{color:var(--amber);}
.th-sort-icon{display:inline-flex;flex-direction:column;gap:1px;vertical-align:middle;margin-left:5px;opacity:.5;}
.th-sorted .th-sort-icon{opacity:1;}
.th-sort-icon svg{width:8px;height:8px;stroke:currentColor;fill:none;stroke-width:2.5;}
.fault-table tbody tr{border-bottom:1px solid var(--border-soft);cursor:pointer;transition:.12s;}
.fault-table tbody tr:last-child{border-bottom:none;}
.fault-table tbody tr:hover{background:var(--panel-alt);}
.fault-table tbody tr.tr-breach{border-left:3px solid var(--red);}
.fault-table tbody tr.tr-warning{border-left:3px solid var(--sla-warn);}
.fault-table tbody tr:not(.tr-breach):not(.tr-warning){border-left:3px solid transparent;}
.fault-table td{padding:11px 14px;vertical-align:middle;}
.td-id{font-family:'Geist Mono',monospace;font-size:11px;color:var(--text-faint);font-weight:600;white-space:nowrap;}
.td-usina{white-space:nowrap;}
.td-client-tag{display:inline-block;font-size:10px;font-family:'Geist Mono',monospace;font-weight:700;padding:2px 7px;border-radius:4px;margin-bottom:4px;}
.td-usina-name{font-size:12.5px;font-weight:600;color:var(--text);}
.td-equip{font-size:13px;font-weight:600;color:var(--text);white-space:nowrap;}
.td-falha{color:var(--text-dim);font-size:12.5px;line-height:1.45;max-width:260px;}
.td-falha-text{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.td-status{white-space:nowrap;}
.td-status-chip{display:inline-block;font-size:10px;font-family:'Geist Mono',monospace;text-transform:uppercase;letter-spacing:.04em;padding:3px 9px;border-radius:5px;font-weight:700;border:1px solid;white-space:nowrap;}
.td-age{white-space:nowrap;}
.td-age-pill{display:inline-flex;align-items:center;gap:5px;font-family:'Geist Mono',monospace;font-size:11px;font-weight:600;padding:3px 9px;border-radius:99px;letter-spacing:.02em;}
.td-age-pill svg{width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:2.2;flex:none;}
.td-age-pill.age-ok{background:rgba(34,197,94,.1);color:var(--sla-ok);border:1px solid rgba(34,197,94,.25);}
.td-age-pill.age-warning{background:rgba(234,179,8,.1);color:var(--sla-warn);border:1px solid rgba(234,179,8,.25);}
.td-age-pill.age-breach{background:rgba(226,84,61,.12);color:var(--red);border:1px solid rgba(226,84,61,.3);}
.td-age-pill.age-none{background:var(--panel-alt);color:var(--text-faint);border:1px solid var(--border-soft);}
.td-ticket{font-family:'Geist Mono',monospace;font-size:11.5px;color:var(--blue);font-weight:600;white-space:nowrap;}
.td-ticket.empty{color:var(--text-faint);font-weight:400;font-style:italic;}
.td-action{text-align:center;}
.td-see-btn{background:transparent;border:1px solid var(--border);color:var(--text-faint);border-radius:6px;padding:5px 10px;font-size:11px;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;}
.td-see-btn svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;}
.fault-table tbody tr:hover .td-see-btn{border-color:var(--amber);color:var(--amber);}
.table-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 16px;border-top:1px solid var(--border-soft);flex-wrap:wrap;}
.page-info{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;}
.page-btns{display:flex;gap:4px;}
.page-btn{background:var(--panel-alt);border:1px solid var(--border);color:var(--text-dim);border-radius:6px;padding:5px 10px;font-size:11px;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;min-width:34px;text-align:center;}
.page-btn:hover:not(:disabled){border-color:var(--amber);color:var(--amber);}
.page-btn.active{border-color:var(--amber);color:var(--amber);background:rgba(242,169,59,.08);font-weight:700;}
.page-btn:disabled{opacity:.35;cursor:not-allowed;}
.rows-select{background:var(--panel-alt);border:1px solid var(--border);color:var(--text-dim);border-radius:6px;padding:5px 8px;font-size:11px;font-family:'Geist Mono',monospace;cursor:pointer;outline:none;}
.rows-select:focus{border-color:var(--amber);}
.client-mode-banner{display:none;align-items:center;gap:12px;padding:11px 16px;margin-bottom:20px;border-radius:var(--radius);border:1px solid;}
.client-mode-banner.visible{display:flex;}
.client-mode-banner svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.client-mode-banner span{font-size:13px;font-family:'Geist Mono',monospace;}
.client-mode-banner strong{font-weight:700;}
.skeleton{background:linear-gradient(90deg,var(--panel-alt) 25%,var(--border) 50%,var(--panel-alt) 75%);background-size:200% 100%;animation:shimmer 1.4s infinite;border-radius:6px;}
@keyframes shimmer{0%{background-position:200% 0;}100%{background-position:-200% 0;}}
.skel-card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:16px;border-bottom:3px solid var(--border);}
.skel-line{height:10px;margin-bottom:10px;}
.skel-title{height:14px;width:70%;margin-bottom:8px;}
.skel-sub{height:10px;width:45%;margin-bottom:14px;}
.skel-foot{height:9px;width:55%;}
.overlay{position:fixed;inset:0;background:rgba(8,12,18,.78);backdrop-filter:blur(3px);display:none;align-items:center;justify-content:center;z-index:100;padding:24px;}
.overlay.open{display:flex;}
.modal{background:var(--panel);border:1px solid var(--border);border-radius:14px;max-width:660px;width:100%;max-height:88vh;overflow-y:auto;box-shadow:0 24px 64px rgba(0,0,0,.6);border-top:4px solid var(--modal-color,var(--amber));animation:modalIn .2s cubic-bezier(.2,.8,.4,1);}
@keyframes modalIn{from{opacity:0;transform:translateY(10px) scale(.98);}to{opacity:1;transform:none;}}
.modal-head{padding:22px 24px 16px;border-bottom:1px solid var(--border-soft);position:sticky;top:0;background:var(--panel);z-index:2;border-radius:10px 10px 0 0;}
.modal-top-bar{display:flex;justify-content:space-between;align-items:flex-start;}
.modal-close{background:var(--panel-alt);border:1px solid var(--border);color:var(--text-faint);width:30px;height:30px;border-radius:8px;font-size:16px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:none;transition:.15s;}
.modal-close:hover{color:var(--text);}
.modal-id{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--text-faint);margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em;}
.modal-head h2{font-size:20px;margin-bottom:4px;line-height:1.2;}
.modal-meta{font-size:13px;color:var(--text-dim);font-family:'Geist Mono',monospace;}
.modal-body{padding:20px 24px 28px;}
.field{margin-bottom:18px;}
.field:last-child{margin-bottom:0;}
.field h4{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);font-family:'Geist Mono',monospace;margin-bottom:7px;font-weight:600;display:flex;align-items:center;gap:8px;}
.field h4::after{content:"";flex:1;height:1px;background:var(--border-soft);}
.field p{font-size:13.5px;line-height:1.6;color:var(--text);}
.chips{display:flex;flex-wrap:wrap;gap:6px;}
.chip{font-size:12px;background:var(--panel-alt);border:1px solid var(--border-soft);border-radius:6px;padding:4px 10px;color:var(--text-dim);}
.action-box{background:var(--panel-alt);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-size:13px;line-height:1.6;color:var(--text);border-left:3px solid var(--modal-color,var(--amber));}
.ticket-box{display:inline-flex;align-items:center;gap:9px;background:rgba(91,155,213,.08);border:1px solid rgba(91,155,213,.25);border-radius:7px;padding:9px 14px;font-family:'Geist Mono',monospace;font-size:13px;color:var(--blue);letter-spacing:.02em;min-height:38px;}
.ticket-box svg{width:14px;height:14px;stroke:var(--blue);fill:none;stroke-width:2;flex:none;}
.ticket-box.empty{background:transparent;border:1px dashed var(--border);color:var(--text-faint);font-size:12px;}
.timeline{position:relative;padding-left:22px;}
.timeline::before{content:"";position:absolute;left:6px;top:6px;bottom:6px;width:1px;background:var(--border);}
.tl-item{position:relative;padding-bottom:16px;}
.tl-item:last-child{padding-bottom:0;}
.tl-dot{position:absolute;left:-22px;top:3px;width:12px;height:12px;border-radius:50%;background:var(--amber);border:2px solid var(--panel);}
.tl-date{font-family:'Geist Mono',monospace;font-size:11px;color:var(--amber);margin-bottom:4px;font-weight:600;}
.tl-text{font-size:13.5px;color:var(--text-dim);line-height:1.55;}
.drawer-backdrop{position:fixed;inset:0;z-index:200;background:rgba(8,12,18,.55);backdrop-filter:blur(2px);opacity:0;pointer-events:none;transition:opacity .28s ease;}
.drawer-backdrop.open{opacity:1;pointer-events:all;}
.drawer{position:fixed;top:0;right:0;bottom:0;z-index:201;width:min(520px,100vw);background:var(--panel);border-left:1px solid var(--border);display:flex;flex-direction:column;transform:translateX(100%);transition:transform .3s cubic-bezier(.4,0,.2,1);box-shadow:-12px 0 48px rgba(0,0,0,.45);}
.drawer.open{transform:translateX(0);}
.drawer-accent{height:4px;flex:none;background:var(--drawer-color,var(--amber));transition:background .2s;}
.drawer-head{padding:18px 20px 14px;border-bottom:1px solid var(--border-soft);flex:none;background:var(--panel);}
.drawer-head-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px;}
.drawer-eyebrow{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--text-faint);letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px;}
.drawer-head h2{font-size:18px;font-weight:700;line-height:1.2;color:var(--text);margin:0;}
.drawer-head-meta{font-size:12.5px;color:var(--text-dim);font-family:'Geist Mono',monospace;margin-top:4px;}
.drawer-head-btns{display:flex;align-items:center;gap:6px;flex:none;}
.drawer-icon-btn{width:30px;height:30px;border-radius:8px;border:1px solid var(--border);background:var(--panel-alt);color:var(--text-faint);display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s;flex:none;}
.drawer-icon-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;}
.drawer-icon-btn:hover{color:var(--text);border-color:var(--text-dim);}
.drawer-icon-btn.close:hover{color:var(--red);border-color:var(--red);}
.drawer-nav{display:flex;align-items:center;gap:6px;background:var(--panel-alt);border:1px solid var(--border);border-radius:8px;padding:3px;}
.drawer-nav-btn{width:26px;height:26px;border-radius:6px;border:none;background:transparent;color:var(--text-faint);display:flex;align-items:center;justify-content:center;cursor:pointer;transition:.15s;flex:none;}
.drawer-nav-btn svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2.2;}
.drawer-nav-btn:hover:not(:disabled){background:var(--panel);color:var(--amber);}
.drawer-nav-btn:disabled{opacity:.3;cursor:not-allowed;}
.drawer-nav-counter{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--text-faint);padding:0 4px;white-space:nowrap;}
.drawer-body{flex:1;overflow-y:auto;padding:18px 20px 32px;scroll-behavior:smooth;}
.drawer-body::-webkit-scrollbar{width:4px;}
.drawer-body::-webkit-scrollbar-track{background:transparent;}
.drawer-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:99px;}
.drawer-age-block{display:flex;align-items:center;gap:12px;border-radius:10px;padding:13px 15px;margin-bottom:20px;border:1px solid;}
.drawer-age-block .age-num{font-family:'Geist Mono',monospace;font-size:30px;font-weight:700;line-height:1;}
.drawer-age-block .age-sub{font-size:12px;color:var(--text-dim);margin-top:3px;font-family:'Geist Mono',monospace;}
.drawer-age-block svg{width:22px;height:22px;fill:none;stroke-width:1.8;flex:none;}
.d-section{margin-bottom:20px;}
.d-section:last-child{margin-bottom:0;}
.d-label{font-size:10px;font-family:'Geist Mono',monospace;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--text-faint);margin-bottom:8px;display:flex;align-items:center;gap:8px;}
.d-label::after{content:"";flex:1;height:1px;background:var(--border-soft);}
.d-value{font-size:13.5px;line-height:1.6;color:var(--text);}
.d-action-box{background:var(--panel-alt);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-size:13px;line-height:1.65;color:var(--text);border-left:3px solid var(--drawer-color,var(--amber));}
.d-timeline{position:relative;padding-left:20px;}
.d-timeline::before{content:"";position:absolute;left:5px;top:5px;bottom:5px;width:1px;background:var(--border);}
.d-tl-item{position:relative;padding-bottom:14px;}
.d-tl-item:last-child{padding-bottom:0;}
.d-tl-dot{position:absolute;left:-20px;top:3px;width:11px;height:11px;border-radius:50%;background:var(--amber);border:2px solid var(--panel);}
.d-tl-date{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--amber);margin-bottom:3px;font-weight:600;}
.d-tl-text{font-size:13px;color:var(--text-dim);line-height:1.55;}
.d-ticket-row{display:flex;gap:10px;flex-wrap:wrap;}
.d-ticket-box{display:inline-flex;align-items:center;gap:8px;flex:1;min-width:140px;border-radius:8px;padding:9px 13px;font-family:'Geist Mono',monospace;font-size:12.5px;letter-spacing:.02em;border:1px solid;}
.d-ticket-box svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.d-ticket-box.t-blue{background:rgba(91,155,213,.08);border-color:rgba(91,155,213,.25);color:var(--blue);}
.d-ticket-box.t-teal{background:rgba(63,193,176,.08);border-color:rgba(63,193,176,.25);color:var(--teal);}
.d-ticket-box.t-empty{background:transparent;border-color:var(--border);color:var(--text-faint);border-style:dashed;font-style:italic;}
.d-ticket-label{font-size:9.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);font-family:'Geist Mono',monospace;margin-bottom:4px;font-weight:700;}
.card.drawer-active{border-color:var(--amber)!important;box-shadow:0 0 0 2px rgba(242,169,59,.25),var(--shadow)!important;}
.fault-table tbody tr.drawer-active{background:rgba(242,169,59,.05)!important;outline:2px solid rgba(242,169,59,.3);outline-offset:-2px;}
@media(min-width:860px){
  body.drawer-open .wrap{padding-right:calc(clamp(16px,3vw,48px) + min(520px,38vw));transition:padding-right .3s cubic-bezier(.4,0,.2,1);}
  body:not(.drawer-open) .wrap{padding-right:clamp(16px,3vw,48px);transition:padding-right .3s cubic-bezier(.4,0,.2,1);}
  .drawer{width:min(520px,38vw);}
  .drawer-backdrop{display:none;}
}
@media(min-width:1600px){body.drawer-open .wrap{padding-right:calc(clamp(16px,3vw,48px) + 520px);}.drawer{width:520px;}}
@media(max-width:859px){.drawer{width:100vw;}}
.pwa-banner{display:none;align-items:center;gap:12px;justify-content:space-between;padding:12px 16px;margin-bottom:20px;background:linear-gradient(135deg,rgba(63,193,176,.1),rgba(91,155,213,.08));border:1px solid rgba(63,193,176,.3);border-radius:var(--radius);}
.pwa-banner.visible{display:flex;}
.pwa-banner-left{display:flex;align-items:center;gap:10px;}
.pwa-banner-left svg{width:18px;height:18px;stroke:var(--teal);fill:none;stroke-width:2;flex:none;}
.pwa-banner-left span{font-size:13px;font-family:'Geist Mono',monospace;color:var(--text-dim);}
.pwa-banner-left strong{color:var(--teal);}
.pwa-install-btn{background:rgba(63,193,176,.15);border:1px solid rgba(63,193,176,.4);color:var(--teal);border-radius:7px;padding:7px 14px;font-size:12px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;}
.pwa-install-btn:hover{background:rgba(63,193,176,.25);}
.pwa-dismiss-btn{background:transparent;border:none;color:var(--text-faint);cursor:pointer;font-size:16px;padding:4px;line-height:1;}
footer{margin-top:36px;text-align:center;font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;line-height:1.9;background:var(--panel);border:1px solid var(--border-soft);border-radius:var(--radius);padding:14px 20px;}
.filter-group.date-group{min-width:130px;}
.date-input{background:var(--panel-alt);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;font-size:12px;font-family:'Geist Mono',monospace;outline:none;transition:.15s;width:100%;cursor:pointer;}
.date-input:focus{border-color:var(--amber);background:rgba(242,169,59,.04);}
.date-input::-webkit-calendar-picker-indicator{filter:invert(0.6);cursor:pointer;}
[data-theme="light"] .date-input::-webkit-calendar-picker-indicator{filter:none;}
.edit-mode-banner{display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:16px;background:rgba(242,169,59,.08);border:1px solid rgba(242,169,59,.25);border-radius:8px;font-size:11.5px;font-family:'Geist Mono',monospace;color:var(--amber);}
.edit-mode-banner svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.d-edit-field{margin-bottom:16px;}
.d-edit-label{font-size:9.5px;font-family:'Geist Mono',monospace;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--text-faint);margin-bottom:6px;display:flex;align-items:center;gap:8px;}
.d-edit-label::after{content:"";flex:1;height:1px;background:var(--border-soft);}
.d-edit-input,.d-edit-textarea,.d-edit-select{width:100%;background:var(--panel-alt);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:9px 12px;font-size:13px;font-family:'Geist',sans-serif;outline:none;transition:.15s;resize:vertical;-webkit-appearance:none;appearance:none;}
.d-edit-select option{background:var(--panel-alt);color:var(--text);}
[data-theme="light"] .d-edit-select option{background:#F5F8FB;color:#1A2533;}
.d-edit-input:focus,.d-edit-textarea:focus,.d-edit-select:focus{border-color:var(--amber);background:rgba(242,169,59,.03);box-shadow:0 0 0 3px rgba(242,169,59,.1);}
.d-edit-textarea{min-height:80px;line-height:1.55;}
.d-edit-select{cursor:pointer;}
.drawer-action-bar{display:flex;gap:8px;padding:14px 20px;border-top:1px solid var(--border-soft);background:var(--panel);flex:none;}
.drawer-save-btn{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;background:linear-gradient(135deg,rgba(242,169,59,.2),rgba(242,169,59,.1));border:1px solid rgba(242,169,59,.45);color:var(--amber);border-radius:8px;padding:10px;font-size:12px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;}
.drawer-save-btn:hover{background:rgba(242,169,59,.3);}
.drawer-save-btn:disabled{opacity:.4;cursor:not-allowed;}
.drawer-save-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2.2;}
.drawer-hist-btn{display:flex;align-items:center;gap:6px;background:rgba(63,193,176,.08);border:1px solid rgba(63,193,176,.3);color:var(--teal);border-radius:8px;padding:10px 14px;font-size:12px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;}
.drawer-hist-btn:hover{background:rgba(63,193,176,.18);}
.drawer-hist-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;}
.save-spinner{display:inline-block;width:12px;height:12px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin .6s linear infinite;flex:none;}
.d-status-preview{display:flex;align-items:center;gap:8px;margin-top:6px;font-family:'Geist Mono',monospace;font-size:11px;}
.d-status-dot{width:8px;height:8px;border-radius:50%;flex:none;}

/* ══ BOTÃO VERIFICAR RONDAS ══ */
.rondas-btn{
  display:none;align-items:center;gap:8px;
  background:linear-gradient(135deg,rgba(91,155,213,.18),rgba(63,193,176,.12));
  border:1px solid rgba(91,155,213,.45);color:#7DC8F0;
  border-radius:8px;padding:9px 16px;font-size:12px;font-weight:700;
  cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;letter-spacing:.04em;
}
.rondas-btn:hover{background:rgba(91,155,213,.28);border-color:#7DC8F0;box-shadow:0 0 16px rgba(91,155,213,.2);}
.rondas-btn:disabled{opacity:.5;cursor:not-allowed;}
.rondas-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.rondas-btn.loading svg{animation:spin .7s linear infinite;}
/* ══ MODAL RONDAS — tela cheia com painel lateral ══ */
.rondas-overlay{position:fixed;inset:0;background:rgba(8,12,18,.88);backdrop-filter:blur(4px);display:none;align-items:stretch;justify-content:center;z-index:200;}
.rondas-overlay.open{display:flex;}

/* Modal principal: ocupa quase toda a tela */
.rondas-modal{
  display:flex;flex-direction:column;
  background:var(--panel);border:1px solid var(--border);
  border-radius:0;width:100%;max-width:100%;
  box-shadow:var(--shadow);
  animation:modalIn .2s cubic-bezier(.2,.8,.4,1);
  overflow:hidden;
}
@media(min-width:640px){
  .rondas-overlay{padding:20px;}
  .rondas-modal{border-radius:16px;max-width:1100px;max-height:calc(100vh - 40px);}
}

/* Cabeçalho do modal */
.rondas-modal-head{
  display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:16px 20px;border-bottom:1px solid var(--border);
  background:var(--panel);flex:none;
}
.rondas-modal-head h3{font-size:15px;font-weight:700;margin:0;display:flex;align-items:center;gap:8px;}
.rondas-modal-head h3 svg{width:16px;height:16px;stroke:#7DC8F0;fill:none;stroke-width:2;flex:none;}
.rondas-modal-close{
  background:var(--panel-alt);border:1px solid var(--border);
  color:var(--text-faint);width:30px;height:30px;border-radius:8px;
  font-size:16px;cursor:pointer;display:flex;align-items:center;
  justify-content:center;flex:none;transition:.15s;
}
.rondas-modal-close:hover{color:var(--red);border-color:var(--red);}

/* Layout de 2 colunas: sidebar grupos + área de mensagens */
.rondas-body{display:flex;flex:1;overflow:hidden;min-height:0;}

/* Sidebar: lista de grupos */
.rondas-sidebar{
  width:220px;flex:none;
  border-right:1px solid var(--border);
  overflow-y:auto;background:var(--panel-alt);
}
@media(max-width:639px){.rondas-sidebar{width:130px;}}
.rondas-sidebar::-webkit-scrollbar{width:3px;}
.rondas-sidebar::-webkit-scrollbar-thumb{background:var(--border);border-radius:99px;}

.rondas-grupo-item{
  display:flex;align-items:center;gap:10px;
  padding:11px 14px;cursor:pointer;
  border-bottom:1px solid var(--border-soft);
  transition:.15s;position:relative;
}
.rondas-grupo-item:hover{background:var(--panel);}
.rondas-grupo-item.active{background:var(--panel);border-left:3px solid #7DC8F0;}
.rondas-grupo-item.active .rg-nome{color:#7DC8F0;}
.rg-dot{width:7px;height:7px;border-radius:50%;flex:none;background:var(--text-faint);}
.rg-dot.has-msgs{background:var(--teal);}
.rg-dot.has-pendentes{background:var(--amber);animation:dotpulse 2s infinite;}
.rg-info{flex:1;min-width:0;}
.rg-nome{font-size:11.5px;font-weight:600;color:var(--text-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.rg-count{font-family:'Geist Mono',monospace;font-size:10px;color:var(--text-faint);margin-top:2px;}

/* Área principal: mensagens do grupo selecionado */
.rondas-main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0;}

.rondas-main-head{
  padding:12px 18px;border-bottom:1px solid var(--border);
  flex:none;display:flex;align-items:center;justify-content:space-between;gap:12px;
  background:var(--panel);
}
.rondas-main-titulo{font-size:13px;font-weight:700;color:#7DC8F0;}
.rondas-main-sub{font-size:11px;color:var(--text-faint);font-family:'Geist Mono',monospace;margin-top:2px;}

/* Lista de mensagens com scroll */
.rondas-msgs-list{
  flex:1;overflow-y:auto;padding:14px 18px;
  display:flex;flex-direction:column;gap:10px;
  /* sem overflow:hidden — cada card tem seu próprio scroll */
}
.rondas-msgs-list::-webkit-scrollbar{width:5px;}
.rondas-msgs-list::-webkit-scrollbar-track{background:transparent;}
.rondas-msgs-list::-webkit-scrollbar-thumb{background:var(--border);border-radius:99px;}
.rondas-msgs-list::-webkit-scrollbar-thumb:hover{background:var(--text-faint);}

/* Card de mensagem individual */
.ronda-msg-card{
  background:var(--panel-alt);border:1px solid var(--border);
  border-radius:10px;overflow:hidden;
  transition:border-color .15s;
}
.ronda-msg-card:hover{border-color:rgba(125,200,240,.25);}
/* Card mais recente — destaque */
.ronda-msg-card.latest{
  border-color:rgba(63,193,176,.35);
  box-shadow:0 0 0 1px rgba(63,193,176,.12);
}
.ronda-msg-card.latest .ronda-msg-card-head{
  background:rgba(63,193,176,.07);
  border-bottom-color:rgba(63,193,176,.2);
}
.ronda-msg-card.latest .ronda-msg-ts{color:var(--teal);}
.ronda-msg-card-head{
  display:flex;align-items:center;justify-content:space-between;gap:8px;
  padding:8px 12px;border-bottom:1px solid var(--border-soft);
  background:rgba(0,0,0,.12);cursor:pointer;user-select:none;
}
.ronda-msg-card-head:hover{background:rgba(0,0,0,.2);}
.ronda-msg-ts{font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--text-faint);}
.ronda-msg-badge{
  font-family:'Geist Mono',monospace;font-size:9.5px;font-weight:700;
  padding:2px 8px;border-radius:99px;
}
.ronda-msg-badge.proc{background:rgba(63,193,176,.12);color:var(--teal);border:1px solid rgba(63,193,176,.3);}
.ronda-msg-badge.pend{background:rgba(234,179,8,.08);color:var(--sla-warn);border:1px solid rgba(234,179,8,.25);}
.ronda-msg-toggle-icon{
  width:20px;height:20px;border-radius:5px;
  background:transparent;border:none;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  color:var(--text-faint);transition:.15s;flex:none;
}
.ronda-msg-toggle-icon svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;transition:transform .2s;}
.ronda-msg-toggle-icon.open svg{transform:rotate(180deg);}

/* Corpo da mensagem — altura fixa com scroll */
.ronda-msg-body{
  padding:0 14px;
  font-size:12.5px;color:var(--text);
  white-space:pre-wrap;word-break:break-word;line-height:1.65;
  height:0;overflow:hidden;
  transition:height .25s cubic-bezier(.4,0,.2,1), padding .25s;
}
.ronda-msg-body.open{
  /* altura padrão para mensagens antigas/menores */
  height:120px;
  overflow-y:auto;
  padding:12px 14px;
}
/* Última mensagem (mais recente) — destaque maior */
.ronda-msg-card.latest .ronda-msg-body.open{
  height:240px;
}
/* Scrollbar */
.ronda-msg-body::-webkit-scrollbar{width:4px;}
.ronda-msg-body::-webkit-scrollbar-track{background:transparent;}
.ronda-msg-body::-webkit-scrollbar-thumb{background:var(--border);border-radius:99px;}
.ronda-msg-body::-webkit-scrollbar-thumb:hover{background:var(--text-faint);}

/* Estado vazio */
.rondas-empty{
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;color:var(--text-faint);gap:10px;padding:40px;
  text-align:center;
}
.rondas-empty svg{width:36px;height:36px;stroke:var(--border);fill:none;stroke-width:1.4;}
.rondas-empty span{font-size:13px;}

/* Rodapé */
.rondas-modal-footer{
  display:flex;gap:8px;padding:12px 18px;
  border-top:1px solid var(--border);flex:none;
  background:var(--panel);flex-wrap:wrap;
}

/* Chips de resultado (tela inicial) */
.rondas-result-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--border-soft);font-family:'Geist Mono',monospace;font-size:12px;cursor:pointer;border-radius:6px;transition:.15s;padding-left:6px;padding-right:6px;}
.rondas-result-row:last-child{border-bottom:none;}
.rondas-result-row.clickable:hover{background:var(--panel-alt);}
.rondas-result-num{font-size:22px;font-weight:700;font-family:'Geist Mono',monospace;}
.rondas-item-list{margin-top:8px;display:none;flex-direction:column;gap:4px;padding:4px 0 4px 12px;}
.rondas-item-list.open{display:flex;}
.rondas-item-chip{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:6px;font-family:'Geist Mono',monospace;font-size:11px;background:var(--panel-alt);border:1px solid var(--border);color:var(--text-dim);cursor:pointer;transition:.15s;}
.rondas-item-chip:hover{border-color:var(--amber);color:var(--amber);}
/* Manter rondas-msg antigo (compatibilidade com tela de resultado) */
.ronda-msg-preview{display:none;}
.ronda-msg-toggle{display:none;}
.rondas-log-btn{display:inline-flex;align-items:center;gap:6px;background:rgba(91,155,213,.08);border:1px solid rgba(91,155,213,.25);color:var(--blue);border-radius:8px;padding:8px 14px;font-size:11px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;text-decoration:none;white-space:nowrap;}
.rondas-log-btn:hover{background:rgba(91,155,213,.18);}
.rondas-log-btn svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}

.processos-btn{display:none;align-items:center;gap:8px;background:linear-gradient(135deg,rgba(63,193,176,.18),rgba(34,197,94,.12));border:1px solid rgba(63,193,176,.5);color:var(--teal);border-radius:8px;padding:9px 16px;font-size:12px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;white-space:nowrap;letter-spacing:.04em;}
.processos-btn:hover{background:rgba(63,193,176,.28);border-color:var(--teal);box-shadow:0 0 16px rgba(63,193,176,.2);}
.processos-btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}

/* Zeladorias */
.zel-overlay{position:fixed;inset:0;z-index:300;background:var(--bg);display:none;flex-direction:column;animation:fadeInFull .2s ease;}
.zel-overlay.open{display:flex;}
@keyframes fadeInFull{from{opacity:0;}to{opacity:1;}}
.zel-topbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px 24px;border-bottom:1px solid var(--border);background:var(--panel);flex:none;flex-wrap:wrap;}
.zel-topbar-left{display:flex;align-items:center;gap:14px;}
.zel-back-btn{display:flex;align-items:center;gap:6px;background:var(--panel-alt);border:1px solid var(--border);color:var(--text-faint);border-radius:8px;padding:7px 12px;font-size:12px;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;}
.zel-back-btn:hover{color:var(--text);border-color:var(--text-dim);}
.zel-back-btn svg{width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2.2;}
.zel-title-block{display:flex;flex-direction:column;}
.zel-eyebrow{font-family:'Geist Mono',monospace;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--teal);font-weight:700;}
.zel-title{font-size:18px;font-weight:700;}
.zel-topbar-right{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.zel-filters{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 24px;background:var(--panel-alt);border-bottom:1px solid var(--border);flex:none;}
.zel-filter-select{background:var(--panel);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;font-size:13px;font-family:'Geist',sans-serif;outline:none;transition:.15s;cursor:pointer;}
.zel-filter-select:focus{border-color:var(--teal);}
.zel-search{background:var(--panel);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;font-size:13px;font-family:'Geist',sans-serif;outline:none;transition:.15s;flex:1;min-width:160px;max-width:280px;}
.zel-search:focus{border-color:var(--teal);}
.zel-content{flex:1;overflow-y:auto;padding:24px;}
.zel-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px;}
@media(max-width:900px){.zel-kpis{grid-template-columns:repeat(2,1fr);}}
.zel-kpi{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;position:relative;overflow:hidden;}
.zel-kpi::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--kc,var(--teal));}
.zel-kpi .zk-num{font-family:'Geist Mono',monospace;font-size:30px;font-weight:700;color:var(--kc,var(--teal));}
.zel-kpi .zk-label{font-size:12px;color:var(--text-dim);margin-top:6px;}
.zel-tabs{display:flex;gap:4px;margin-bottom:20px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:4px;}
.zel-tab{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;background:transparent;border:none;color:var(--text-faint);border-radius:7px;padding:9px 14px;font-size:12px;font-weight:600;cursor:pointer;font-family:'Geist Mono',monospace;transition:.15s;text-transform:uppercase;letter-spacing:.06em;}
.zel-tab svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;flex:none;}
.zel-tab .zel-tab-badge{background:var(--border);color:var(--text-faint);border-radius:99px;font-size:10px;padding:1px 6px;font-weight:700;}
.zel-tab.active{background:var(--panel-alt);color:var(--tab-color,var(--teal));box-shadow:0 1px 4px rgba(0,0,0,.15);}
.zel-tab.active .zel-tab-badge{background:var(--tab-color,var(--teal));color:#0E1419;}
.zel-tab:hover:not(.active){color:var(--text);}
.zel-table-wrap{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;overflow-x:auto;}
.zel-table{width:100%;border-collapse:collapse;font-size:13px;min-width:780px;}
.zel-table thead tr{background:var(--panel-alt);border-bottom:2px solid var(--border);}
.zel-table th{padding:10px 14px;text-align:left;font-family:'Geist Mono',monospace;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-faint);white-space:nowrap;}
.zel-table td{padding:12px 14px;vertical-align:middle;border-bottom:1px solid var(--border-soft);}
.zel-table tbody tr:last-child td{border-bottom:none;}
.zel-table tbody tr{transition:.12s;cursor:default;}
.zel-table tbody tr:hover{background:var(--panel-alt);}
.zs-chip{display:inline-flex;align-items:center;gap:5px;font-family:'Geist Mono',monospace;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:4px 10px;border-radius:99px;border:1px solid;white-space:nowrap;cursor:pointer;transition:.15s;}
.zs-chip:hover{filter:brightness(1.15);}
.zs-chip svg{width:9px;height:9px;stroke:currentColor;fill:none;stroke-width:2.5;flex:none;}
.zs-agendado{background:rgba(91,155,213,.12);color:var(--blue);border-color:rgba(91,155,213,.3);}
.zs-em-andamento{background:rgba(234,179,8,.12);color:var(--sla-warn);border-color:rgba(234,179,8,.3);}
.zs-concluido{background:rgba(34,197,94,.12);color:var(--sla-ok);border-color:rgba(34,197,94,.3);}
.zs-atrasado{background:rgba(226,84,61,.12);color:var(--red);border-color:rgba(226,84,61,.3);animation:sla-pulse-border 2.5s infinite;}
.zs-pendente{background:rgba(107,119,128,.1);color:var(--gray);border-color:rgba(107,119,128,.25);}
.zs-select{background:transparent;border:none;font-family:'Geist Mono',monospace;font-size:10.5px;font-weight:700;text-transform:uppercase;cursor:pointer;outline:none;color:inherit;padding:0 2px;appearance:none;-webkit-appearance:none;}
.zel-date{font-family:'Geist Mono',monospace;font-size:11.5px;color:var(--text-dim);}
.zel-date.overdue{color:var(--red);font-weight:600;}
.zel-usina-cell{font-size:13px;font-weight:600;color:var(--text);}
.zel-cliente-tag{display:inline-block;font-size:9.5px;font-family:'Geist Mono',monospace;font-weight:700;padding:1px 6px;border-radius:3px;margin-right:6px;vertical-align:middle;}
.zel-obs-input{background:transparent;border:none;border-bottom:1px dashed var(--border);color:var(--text-dim);font-size:12px;font-family:'Geist',sans-serif;width:100%;outline:none;padding:2px 4px;transition:.15s;}
.zel-obs-input:focus{border-bottom-color:var(--teal);color:var(--text);background:var(--panel-alt);border-radius:4px 4px 0 0;}
.zel-add-btn{display:flex;align-items:center;gap:7px;background:rgba(63,193,176,.1);border:1px dashed rgba(63,193,176,.4);color:var(--teal);border-radius:8px;padding:10px 16px;font-size:12px;font-weight:700;cursor:pointer;font-family:'Geist Mono',monospace;transition:.2s;width:100%;margin-top:10px;justify-content:center;}
.zel-add-btn:hover{background:rgba(63,193,176,.2);border-style:solid;}
.zel-add-btn svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;}
.zel-empty{text-align:center;padding:56px 20px;color:var(--text-faint);border:1px dashed var(--border);border-radius:var(--radius);margin-top:4px;}
.zel-empty svg{width:36px;height:36px;stroke:var(--border);fill:none;stroke-width:1.4;margin-bottom:12px;}
.zel-empty strong{display:block;color:var(--text-dim);font-size:14px;margin-bottom:6px;}
.save-badge{display:inline-flex;align-items:center;gap:5px;font-family:'Geist Mono',monospace;font-size:11px;color:var(--teal);opacity:0;transition:opacity .3s;}
.save-badge.show{opacity:1;}
.save-badge svg{width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2.2;}
@media(min-width:1600px){.kpis{grid-template-columns:repeat(4,1fr);}}
@media(max-width:960px){.kpis{grid-template-columns:repeat(2,1fr);}.panels{grid-template-columns:1fr 1fr;}}

/* ══ MOBILE (≤ 680px) ══ */
@media(max-width:680px){
  /* Layout geral */
  .wrap{padding:12px 10px 72px;}
  header{flex-direction:column;align-items:flex-start;gap:10px;}
  h1{font-size:20px;}
  .sub{font-size:12px;}

  /* Header-right: empilha verticalmente */
  .header-right{
    align-items:flex-start;
    flex-direction:row;
    flex-wrap:wrap;
    gap:6px;
    width:100%;
  }
  .header-right .theme-toggle{order:1;}
  .header-right .notif-btn{order:2;font-size:10px;padding:5px 8px;}
  .header-right .source-badge{order:3;font-size:10px;}
  .header-right .countdown-wrap{order:4;display:none !important;}
  .header-right .update-btn{order:5;font-size:11px;padding:7px 10px;}
  .header-right .total-badge{display:none;}
  /* Botão sair */
  .header-right button[onclick]{order:0;font-size:10px;padding:4px 8px;}

  /* KPIs: 2 colunas */
  .kpis{grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:14px;}
  .kpi{padding:12px 14px;}
  .kpi .num{font-size:26px;}
  .kpi .label{font-size:11px;}
  .kpi .kicon{display:none;}

  /* Barra de ações: scroll horizontal */
  .action-bar{
    flex-wrap:nowrap;
    overflow-x:auto;
    gap:8px;
    padding:10px 12px;
    -webkit-overflow-scrolling:touch;
    scrollbar-width:none;
  }
  .action-bar::-webkit-scrollbar{display:none;}
  .action-bar-label{display:none;}
  .rondas-btn,.chamados-btn,.processos-btn{
    font-size:11px;padding:7px 12px;white-space:nowrap;flex:none;
  }

  /* Filtros: empilha */
  .filters{flex-direction:column;gap:8px;padding:10px 12px;}
  .ms-wrap{min-width:unset;width:100%;}
  .filter-group.search-group{min-width:unset;width:100%;}
  .filter-group.date-group{min-width:unset;width:calc(50% - 4px);}
  .filters .filter-group.date-group:last-of-type{margin-left:auto;}
  .result-count{display:none;}

  /* Filtros de data: lado a lado */
  .filters{flex-wrap:wrap;}

  /* Banners */
  .sla-banner,.deslig-banner{font-size:11px;padding:8px 12px;}
  .deslig-banner-count{font-size:22px;}

  /* Abas + controles */
  .grid-controls{flex-direction:column;gap:8px;align-items:flex-start;}
  .grid-controls > div:first-child{width:100%;}
  .grid-controls > div:last-child{width:100%;justify-content:space-between;}
  .tabs-wrapper{width:100%;}
  .tab-btn{flex:1;justify-content:center;font-size:11px;padding:6px 8px;}
  .sort-group{flex-wrap:nowrap;overflow-x:auto;width:100%;-webkit-overflow-scrolling:touch;scrollbar-width:none;}
  .sort-group::-webkit-scrollbar{display:none;}
  .sort-btn{white-space:nowrap;flex:none;}
  .view-toggle{flex:none;}

  /* Panels: 1 coluna */
  .panels{grid-template-columns:1fr;}

  /* Cards: 1 coluna */
  .card-grid{grid-template-columns:1fr;}

  /* Drawer: tela cheia */
  .drawer{width:100vw;}

  /* Legenda de clientes: scroll */
  .client-legend{overflow-x:auto;flex-wrap:nowrap;-webkit-overflow-scrolling:touch;}
  .legend-item{flex:none;}

  /* Login: ajuste para tela pequena */
  .login-box{padding:28px 22px 24px;}
  .login-title{font-size:17px;}
}
@media(prefers-reduced-motion:reduce){.status-led.pulse,.update-btn.spinning svg,.source-dot,.card.sla-breach{animation:none;}*{transition:none!important;}}
[data-theme="light"] .fault-table th{background:var(--panel-alt);}
[data-theme="light"] .fault-table tbody tr:hover{background:#EEF4FA;}
[data-theme="light"] .card:hover{box-shadow:0 4px 16px rgba(0,0,0,.12),0 0 0 1px var(--card-color,transparent);}
[data-theme="light"] select,[data-theme="light"] input[type="text"]{color:var(--text);}
[data-theme="light"] .modal{box-shadow:0 12px 48px rgba(0,0,0,.2);}
[data-theme="light"] .drawer{box-shadow:-8px 0 32px rgba(0,0,0,.15);}
[data-theme="light"] .zel-overlay{background:var(--bg);}
[data-theme="light"] .zel-table tbody tr:hover{background:#EEF4FA;}
[data-theme="light"] .d-edit-input,[data-theme="light"] .d-edit-textarea,[data-theme="light"] .d-edit-select{background:#F5F8FB;}

/* ══ NOTIFICAÇÕES ══ */
.notif-btn {
  display:inline-flex; align-items:center; gap:7px;
  background:rgba(63,193,176,.08); border:1px solid rgba(63,193,176,.25);
  color:var(--teal); border-radius:8px; padding:6px 12px;
  font-size:11px; font-weight:700; cursor:pointer;
  font-family:'Geist Mono',monospace; transition:.2s; white-space:nowrap;
  position:relative;
}
.notif-btn:hover { background:rgba(63,193,176,.18); border-color:var(--teal); }
/* Ativas: fundo sólido teal */
.notif-btn.enabled {
  background:rgba(63,193,176,.2); border-color:var(--teal);
  box-shadow:0 0 0 1px rgba(63,193,176,.15);
}
/* Hover quando ativas: mostra "Desativar" */
.notif-btn.enabled:hover {
  background:rgba(226,84,61,.12); border-color:rgba(226,84,61,.4);
  color:var(--red);
}
.notif-btn.enabled:hover #notifBtnLabel::before { content:'Desativar '; }
.notif-btn.disabled { opacity:.4; cursor:not-allowed; pointer-events:none; }
.notif-btn svg { width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;flex:none; }

/* ══ ASSINATURA ══ */
.assinatura-img {
  height: 52px;
  width: auto;
  display: block;
  /* Dark: imagem preta → branca via invert */
  filter: invert(1) brightness(2);
  opacity: 0.85;
  mix-blend-mode: screen;
}
[data-theme="light"] .assinatura-img {
  /* Light: imagem preta permanece preta */
  filter: none;
  opacity: 0.80;
  mix-blend-mode: multiply;
}


/* ══ ABAS ATIVAS / HISTÓRICO ══ */
.tabs-wrapper {
  display:flex; align-items:center; gap:4px;
  background:var(--panel-alt); border:1px solid var(--border);
  border-radius:8px; padding:3px; margin-right:auto;
}
.tab-btn {
  display:flex; align-items:center; gap:6px;
  background:transparent; border:none; color:var(--text-faint);
  border-radius:6px; padding:6px 14px; font-size:12px; font-weight:600;
  cursor:pointer; font-family:'Geist Mono',monospace;
  transition:.15s; white-space:nowrap;
}
.tab-btn svg { width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;flex:none; }
.tab-btn.active { background:var(--panel); color:var(--amber); box-shadow:0 1px 4px rgba(0,0,0,.2); }
.tab-btn:hover:not(.active) { color:var(--text); }
.tab-count {
  background:var(--border); color:var(--text-faint);
  border-radius:99px; font-size:10px; font-weight:700;
  padding:1px 6px; font-family:'Geist Mono',monospace;
}
.tab-btn.active .tab-count { background:var(--amber); color:#0E1419; }

/* Banner histórico */
.historico-banner {
  display:none; align-items:center; gap:10px;
  padding:10px 16px; margin-bottom:16px;
  background:rgba(107,119,128,.08); border:1px solid rgba(107,119,128,.25);
  border-radius:var(--radius); font-size:12px;
  font-family:'Geist Mono',monospace; color:var(--text-faint);
}
.historico-banner.visible { display:flex; }
.historico-banner svg { width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2;flex:none; }

/* ══ DESLIGAMENTOS ══ */
.card.card-desligamento {
  border-color: #FF4444 !important;
  border-width: 2px !important;
  border-left-width: 4px !important;
  box-shadow: 0 0 0 1px rgba(255,68,68,.2), 0 0 24px rgba(255,68,68,.1) !important;
  animation: deslig-pulse 2s infinite;
}
@keyframes deslig-pulse {
  0%,100% { box-shadow: 0 0 0 1px rgba(255,68,68,.2), 0 0 16px rgba(255,68,68,.08); }
  50%      { box-shadow: 0 0 0 2px rgba(255,68,68,.4), 0 0 32px rgba(255,68,68,.18); }
}
.deslig-badge {
  display:inline-flex; align-items:center; gap:5px;
  font-family:'Geist Mono',monospace; font-size:10px; font-weight:700;
  letter-spacing:.06em; padding:3px 9px; border-radius:99px;
  background:rgba(255,68,68,.18); color:#FF6B6B;
  border:1px solid rgba(255,68,68,.4);
  text-transform:uppercase;
}
.deslig-badge svg { width:9px;height:9px;stroke:currentColor;fill:none;stroke-width:2.5;flex:none; }

/* Banner de desligamentos */
.deslig-banner {
  display:none; align-items:center; gap:12px;
  padding:12px 18px; margin-bottom:16px;
  background:linear-gradient(135deg,rgba(255,68,68,.12),rgba(226,84,61,.08));
  border:1px solid rgba(255,68,68,.4); border-radius:var(--radius);
  cursor:pointer; user-select:none;
  animation: deslig-border-pulse 2s infinite;
}
.deslig-banner.visible { display:flex; }
.deslig-banner.active-filter { background:rgba(255,68,68,.2); border-color:#FF4444; }
.deslig-banner:hover { background:rgba(255,68,68,.18); }
@keyframes deslig-border-pulse {
  0%,100% { border-color:rgba(255,68,68,.4); }
  50%      { border-color:rgba(255,68,68,.85); }
}
.deslig-banner-icon {
  width:36px; height:36px; border-radius:10px;
  background:rgba(255,68,68,.15); border:1px solid rgba(255,68,68,.3);
  display:flex; align-items:center; justify-content:center; flex:none;
}
.deslig-banner-icon svg { width:18px;height:18px;stroke:#FF6B6B;fill:none;stroke-width:2;flex:none; }
.deslig-banner-count {
  font-family:'Geist Mono',monospace; font-size:28px; font-weight:700;
  color:#FF4444; line-height:1; flex:none;
}
.deslig-banner-text { flex:1; }
.deslig-banner-title {
  font-size:14px; font-weight:700; color:#FF6B6B; margin-bottom:2px;
}
.deslig-banner-sub {
  font-size:11px; color:var(--text-faint); font-family:'Geist Mono',monospace;
}
.deslig-filter-hint {
  font-size:11px; color:rgba(255,107,107,.6); font-family:'Geist Mono',monospace;
  white-space:nowrap;
}

/* Barra de ações (abaixo dos KPIs) */
.action-bar {
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  padding:12px 16px; margin-bottom:20px;
  background:var(--panel); border:1px solid var(--border);
  border-radius:var(--radius);
}
.action-bar-label {
  font-size:10px; font-family:'Geist Mono',monospace; font-weight:700;
  text-transform:uppercase; letter-spacing:.1em; color:var(--text-faint);
  margin-right:4px; white-space:nowrap;
}
.action-bar .chamados-btn,
.action-bar .processos-btn,
.action-bar .rondas-btn { display:none; }

/* KPI desligamentos */
.kpi.kpi-deslig {
  --kpi-color:#FF4444;
  --kpi-glow:rgba(255,68,68,.12);
  cursor:pointer;
}
.kpi.kpi-deslig:hover { border-color:rgba(255,68,68,.4); }

</style>
</head>
<body>

<!-- LOGIN -->
<div id="loginScreen">
  <div class="login-box">
    <div class="login-logo"><svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></div>
    <div class="login-title">Painel O&amp;M</div>
    <div class="login-sub">Acompanhamento de Falhas</div>
    <div class="login-field"><label for="loginUser">Usuário</label><input type="text" id="loginUser" placeholder="seu usuário" autocomplete="username"></div>
    <div class="login-field"><label for="loginPass">Senha</label><input type="password" id="loginPass" placeholder="••••••••" autocomplete="current-password"></div>
    <div class="login-error" id="loginError"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Usuário ou senha incorretos.</div>
    <button class="login-submit" id="loginSubmit">Entrar</button>
    <div class="login-footer">Acesso restrito · Grid Co O&amp;M</div>
  </div>
</div>

<div class="wrap">
  <header>
    <div>
      <p class="eyebrow">Operação &amp; Manutenção · Painel de Controle</p>
      <h1>Acompanhamento de Falhas</h1>
      <div class="sub">Visualização e controle de falhas nos ativos — clique em um card para ver o histórico</div>
    </div>
    <div class="header-right">
      <button onclick="sessionStorage.clear();location.reload();" style="background:transparent;border:1px solid var(--border);color:var(--text-faint);border-radius:7px;padding:5px 10px;font-size:11px;cursor:pointer;font-family:'Geist Mono',monospace;" title="Sair">⎋ Sair</button>
      <div class="theme-toggle" id="themeToggle" title="Alternar tema claro/escuro">
        <div class="theme-toggle-track"><div class="theme-toggle-thumb"></div></div>
        <span class="theme-toggle-label" id="themeLabel">☾ Escuro</span>
      </div>
      <div class="source-badge" id="sourceBadge"><div class="source-dot" id="sourceDot"></div><span id="sourceLabel">aguardando…</span></div>
      <div class="countdown-wrap" id="countdownWrap" style="display:none">
        <svg viewBox="0 0 24 24" style="width:12px;height:12px;stroke:var(--text-faint);fill:none;stroke-width:2;flex:none;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <div class="countdown-bar-track"><div class="countdown-bar-fill" id="countdownFill" style="width:100%"></div></div>
        <span id="countdownText">5:00</span>
      </div>
      <button class="notif-btn" id="notifBtn" title="Ativar notificações push">
        <svg viewBox="0 0 24 24"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
        <span id="notifBtnLabel">Notificações</span>
      </button>
      <button class="update-btn" id="updateBtn">
        <svg viewBox="0 0 24 24" id="updateIcon"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>
        Atualizar dados
      </button>
      <div class="total-badge" id="totalBadge"></div>
    </div>
  </header>

  <div class="pwa-banner" id="pwaBanner">
    <div class="pwa-banner-left">
      <svg viewBox="0 0 24 24"><path d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2z"/><path d="M12 8v4l3 3"/></svg>
      <span>Instale o <strong>Painel O&M</strong> no seu celular para acesso rápido mesmo offline</span>
    </div>
    <div style="display:flex;align-items:center;gap:8px;">
      <button class="pwa-install-btn" id="pwaInstallBtn">Instalar app</button>
      <button class="pwa-dismiss-btn" id="pwaDismissBtn" title="Fechar">✕</button>
    </div>
  </div>

  <div class="client-mode-banner" id="clientModeBanner">
    <svg viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
    <span>Visualização exclusiva para <strong id="clientModeLabel"></strong> — apenas suas usinas são exibidas</span>
  </div>

  <div class="sla-banner" id="slaBanner">
    <svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
    <span id="slaBannerText" style="flex:1"></span>
    <span style="font-size:11px;opacity:.7;white-space:nowrap;">clique para filtrar ↗</span>
  </div>

  <!-- Banner de desligamentos — aparece acima dos KPIs quando há desligamentos ativos -->
  <div class="deslig-banner" id="desligBanner">
    <div class="deslig-banner-icon">
      <svg viewBox="0 0 24 24"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>
    </div>
    <div class="deslig-banner-count" id="desligCount">0</div>
    <div class="deslig-banner-text">
      <div class="deslig-banner-title" id="desligTitle">USINA(S) DESLIGADA(S)</div>
      <div class="deslig-banner-sub" id="desligSub">Geração total comprometida — ação imediata necessária</div>
    </div>
    <span class="deslig-filter-hint">clique para filtrar ↗</span>
  </div>

  <div class="kpis" id="kpis"></div>

  <!-- Barra de ações — logo abaixo dos KPIs -->
  <div class="action-bar" id="actionBar" style="display:none">
    <span class="action-bar-label">Ações rápidas</span>
    <button class="rondas-btn" id="rondasBtn">
      <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      Verificar Rondas
    </button>
    <button class="chamados-btn" id="chamadosBtn">
      <svg viewBox="0 0 24 24"><path d="M20 12V22H4V12"/><path d="M22 7H2v5h20V7z"/><path d="M12 22V7"/><path d="M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7z"/><path d="M12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7z"/></svg>
      CHAMADOS <span class="chamados-badge" id="chamadosBadge">0</span>
    </button>
    <button class="processos-btn" id="processosBtn">
      <svg viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
      Controle de Processos
    </button>
  </div>

  <div class="filters">
    <div class="ms-wrap" id="ms-cliente"><div class="ms-label">Cliente</div><div class="ms-trigger" id="ms-cliente-trigger"><span class="ms-trigger-text" id="ms-cliente-text">Todos</span><svg class="ms-arrow" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg></div><div class="ms-dropdown" id="ms-cliente-drop"></div></div>
    <div class="ms-wrap" id="ms-usina"><div class="ms-label">Usina</div><div class="ms-trigger" id="ms-usina-trigger"><span class="ms-trigger-text" id="ms-usina-text">Todas</span><svg class="ms-arrow" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg></div><div class="ms-dropdown" id="ms-usina-drop"></div></div>
    <div class="ms-wrap" id="ms-status"><div class="ms-label">Status</div><div class="ms-trigger" id="ms-status-trigger"><span class="ms-trigger-text" id="ms-status-text">Todos</span><svg class="ms-arrow" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg></div><div class="ms-dropdown" id="ms-status-drop"></div></div>
    <div class="filter-group search-group"><label for="fSearch">Buscar equipamento, falha, causa, OS ou ticket</label><input type="text" id="fSearch" placeholder="ex: tracker, SOL-10842, OS 1596…"></div>
    <div class="filter-group date-group"><label for="fDateFrom">A partir de</label><input type="date" class="date-input" id="fDateFrom"></div>
    <div class="filter-group date-group"><label for="fDateTo">Até</label><input type="date" class="date-input" id="fDateTo"></div>
    <button class="btn-ghost" id="clearBtn">✕ Limpar</button>
    <div class="result-count" id="resultCount"></div>
  </div>

  <div class="client-legend" id="clientLegend"><span class="legend-label">Clientes:</span></div>

  <div class="panels">
    <div class="panel"><div class="panel-head"><h3>Falhas por usina</h3><span class="panel-total" id="usinaTotal"></span></div><div id="usinaBars"></div></div>
    <div class="panel"><div class="panel-head"><h3>Por status</h3><span class="panel-total" id="statusTotal"></span></div><div id="statusBars"></div></div>
    <div class="panel"><div class="panel-head"><h3>Por causa</h3><span class="panel-total" id="causaTotal"></span></div><div id="causaBars"></div></div>
  </div>

  <!-- Banner de histórico -->
  <div class="historico-banner" id="historicoBanner">
    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
    <span>Exibindo ocorrências concluídas — <strong id="historicoCount">0</strong> registro(s)</span>
  </div>

  <div class="grid-controls">
    <div style="display:flex;align-items:center;gap:12px;flex:1;flex-wrap:wrap;">
      <!-- Abas Ativas / Histórico -->
      <div class="tabs-wrapper">
        <button class="tab-btn active" id="tabAtivas">
          <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          Ativas
          <span class="tab-count" id="tabAtivasCount">0</span>
        </button>
        <button class="tab-btn" id="tabHistorico">
          <svg viewBox="0 0 24 24"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>
          Histórico
          <span class="tab-count" id="tabHistoricoCount">0</span>
        </button>
      </div>
      <div class="grid-title"><span id="gridSub"></span></div>
    </div>
    <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
      <div class="view-toggle">
        <button class="view-btn active" id="viewCards"><svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>Cards</button>
        <button class="view-btn" id="viewTable"><svg viewBox="0 0 24 24"><path d="M3 3h18v18H3zM3 9h18M3 15h18M9 3v18M15 3v18"/></svg>Tabela</button>
      </div>
      <div class="sort-group" id="sortGroup">
        <span class="sort-label">ordenar:</span>
        <button class="sort-btn active" data-sort="id">ID</button>
        <button class="sort-btn" data-sort="newest">Registradas recentemente</button>
        <button class="sort-btn" data-sort="usina">Usina</button>
        <button class="sort-btn" data-sort="equip">Equipamento</button>
        <button class="sort-btn" data-sort="age">Mais antigas</button>
        <button class="sort-btn" data-sort="recent">Por data ocorrência</button>
      </div>
    </div>
  </div>
  <div class="card-grid" id="cardGrid"></div>
  <div class="table-wrap" id="tableWrap" style="display:none"></div>

  <footer>
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:24px;">
      <div style="text-align:left;line-height:1.9;">
        As informações contidas neste painel são de caráter confidencial e destinam-se exclusivamente a pessoas autorizadas.<br>
        Em conformidade com a Lei Geral de Proteção de Dados (LGPD — Lei nº 13.709/2018), é expressamente proibido o compartilhamento,<br>
        reprodução ou divulgação destes dados a terceiros não autorizados.
      </div>
      <div style="display:flex;flex-direction:column;align-items:center;gap:6px;flex:none;">
        <img src="data:image/png;base64,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"
             alt="Assinatura Fred Alexandrino"
             class="assinatura-img">
        <div style="font-family:'Geist Mono',monospace;font-size:9.5px;color:var(--text-faint);text-align:center;line-height:1.5;text-transform:uppercase;letter-spacing:.04em;">
          Eng. Eletricista · Supervisor de O&amp;M
        </div>
      </div>
    </div>
  </footer>
</div>

<!-- Modal Chamados -->
<div class="ch-overlay" id="chOverlay">
  <div class="ch-modal" id="chModal">
    <div class="ch-head">
      <div class="ch-head-row">
        <div><div class="ch-title"><svg viewBox="0 0 24 24"><path d="M20 12V22H4V12"/><path d="M22 7H2v5h20V7z"/><path d="M12 22V7"/></svg><h2>Chamados — TICKET FABRICANTE</h2></div><div class="ch-subtitle" id="chSubtitle"></div></div>
        <button class="modal-close-btn" id="chClose">✕</button>
      </div>
    </div>
    <div class="ch-body" id="chBody"></div>
  </div>
</div>

<!-- Zeladorias -->
<div class="zel-overlay" id="zelOverlay">
  <div class="zel-topbar">
    <div class="zel-topbar-left">
      <button class="zel-back-btn" id="zelBack"><svg viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"/></svg>Voltar ao Painel</button>
      <div class="zel-title-block"><span class="zel-eyebrow">Controle de Processos</span><span class="zel-title">Programação de Zeladorias</span></div>
    </div>
    <div class="zel-topbar-right">
      <span class="save-badge" id="zelSaveBadge"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>Salvo</span>
      <div class="source-badge" id="zelSourceBadge" style="font-size:10.5px;"><div class="source-dot" id="zelSourceDot"></div><span id="zelSourceLabel">aguardando…</span></div>
      <button class="update-btn" id="zelRefreshBtn" style="padding:7px 12px;font-size:11px;"><svg viewBox="0 0 24 24"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>Atualizar</button>
      <button class="update-btn" id="zelAddRow" style="background:rgba(63,193,176,.1);border-color:rgba(63,193,176,.4);color:var(--teal);"><svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>Nova Usina</button>
    </div>
  </div>
  <div class="zel-filters">
    <select class="zel-filter-select" id="zelFiltroCliente"><option value="">Todos os clientes</option></select>
    <select class="zel-filter-select" id="zelFiltroStatus"><option value="">Todos os status</option><option>Agendado</option><option>Em Andamento</option><option>Concluído</option><option>Atrasado</option><option>Pendente</option></select>
    <input class="zel-search" type="text" id="zelSearch" placeholder="Buscar usina…">
  </div>
  <div class="zel-content" id="zelContent">
    <div class="zel-kpis" id="zelKpis"></div>
    <div class="zel-tabs" id="zelTabs">
      <button class="zel-tab active" data-tab="supressao" style="--tab-color:var(--sla-ok)"><svg viewBox="0 0 24 24"><path d="M12 22V12"/><path d="M5 12H2a10 10 0 0 0 20 0h-3"/><path d="M8 6l4-4 4 4"/><path d="M8 12l4-4 4 4"/></svg>Roçada<span class="zel-tab-badge" id="tabBadgeSupressao">0</span></button>
      <button class="zel-tab" data-tab="poda" style="--tab-color:var(--amber)"><svg viewBox="0 0 24 24"><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/><line x1="8.12" y1="8.12" x2="12" y2="12"/></svg>Poda Química<span class="zel-tab-badge" id="tabBadgePoda">0</span></button>
      <button class="zel-tab" data-tab="limpeza" style="--tab-color:var(--blue)"><svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>Lavagem dos Módulos<span class="zel-tab-badge" id="tabBadgeLimpeza">0</span></button>
    </div>
    <div class="zel-table-wrap" id="zelTableWrap"></div>
  </div>
</div>

<!-- Modal Verificar Rondas -->
<div class="rondas-overlay" id="rondasOverlay">
  <div class="rondas-modal" id="rondasModal">

    <!-- Cabeçalho -->
    <div class="rondas-modal-head">
      <h3>
        <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span id="rondasModalTitulo">Verificar Rondas</span>
      </h3>
      <button class="rondas-modal-close" id="rondasCloseBtn" title="Fechar">✕</button>
    </div>

    <!-- Corpo: resultado OU visualizador por grupo -->
    <div id="rondasResult" style="padding:24px;overflow-y:auto;flex:1;"></div>

    <!-- Corpo: layout 2 colunas (grupos) — oculto por padrão -->
    <div class="rondas-body" id="rondasBodyGrupos" style="display:none;">
      <!-- Sidebar de grupos -->
      <div class="rondas-sidebar" id="rondasSidebar"></div>
      <!-- Área de mensagens -->
      <div class="rondas-main">
        <div class="rondas-main-head">
          <div>
            <div class="rondas-main-titulo" id="rondasGrupoTitulo">Selecione um grupo</div>
            <div class="rondas-main-sub" id="rondasGrupoSub"></div>
          </div>
          <button class="rondas-log-btn" onclick="verificarRondas()" style="white-space:nowrap;">
            <svg viewBox="0 0 24 24"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>
            Resultado
          </button>
        </div>
        <div class="rondas-msgs-list" id="rondasMsgsList">
          <div class="rondas-empty">
            <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            <span>Selecione um grupo na lista</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Rodapé -->
    <div class="rondas-modal-footer" id="rondasFooter">
      <a class="rondas-log-btn" href="https://docs.google.com/spreadsheets/d/1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs/edit#gid=0" target="_blank">
        <svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
        Ver Log de Mensagens
      </a>
      <button class="rondas-log-btn" id="btnBuscarGrupos" style="background:rgba(242,169,59,.08);border-color:rgba(242,169,59,.25);color:var(--amber);">
        <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        Últimas rondas por grupo
      </button>
    </div>

  </div>
</div>

<!-- Drawer -->
<div class="drawer-backdrop" id="drawerBackdrop"></div>
<aside class="drawer" id="drawer" role="complementary" aria-label="Detalhes da ocorrência">
  <div class="drawer-accent" id="drawerAccent"></div>
  <div class="drawer-head" id="drawerHead">
    <div class="drawer-head-top">
      <div style="flex:1;min-width:0;">
        <div class="drawer-eyebrow" id="drawerEyebrow"></div>
        <h2 id="drawerTitle"></h2>
        <div class="drawer-head-meta" id="drawerMeta"></div>
      </div>
      <div class="drawer-head-btns">
        <div class="drawer-nav">
          <button class="drawer-nav-btn" id="drawerPrev"><svg viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"/></svg></button>
          <span class="drawer-nav-counter" id="drawerCounter">1 / 1</span>
          <button class="drawer-nav-btn" id="drawerNext"><svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
        <button class="drawer-icon-btn close" id="drawerClose"><svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </div>
    </div>
  </div>
  <div class="drawer-body" id="drawerBody"></div>
  <div class="drawer-action-bar" id="drawerActionBar" style="display:none">
    <button class="drawer-hist-btn" id="drawerHistBtn"><svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>Adicionar ao histórico</button>
    <button class="drawer-save-btn" id="drawerSaveBtn"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>Salvar na planilha</button>
  </div>
</aside>

<div class="toast" id="toast"><svg viewBox="0 0 24 24" id="toastIcon"></svg><span id="toastMsg"></span></div>

<script>
/* ══ ERRO GLOBAL — captura qualquer erro JS ══ */
window.onerror = function(msg, src, line, col, err) {
  document.getElementById('loginError').textContent = 'Erro JS linha ' + line + ': ' + msg;
  document.getElementById('loginError').style.display = 'flex';
  document.getElementById('loginError').classList.add('show');
  console.error('JS ERROR:', msg, 'linha:', line, err);
  return false;
};
window.addEventListener('unhandledrejection', function(e) {
  console.error('Promise rejected:', e.reason);
});
/* ══ LOGIN ══ */
(function setupLogin() {
  const USERS = [
    { user: 'admin',          pass: 'gridco2026',  role: 'admin'   },
    { user: 'fredalexandrino',pass: 'Fred2004@',   role: 'manager' },
    { user: 'thopen',         pass: 'thopen2026',  role: 'client', cliente: 'THOPEN'   },
    { user: 'renogrid',       pass: 'reno2026',    role: 'client', cliente: 'RENOGRID' },
  ];
  function tryLogin() {
    var u = document.getElementById('loginUser').value.trim().toLowerCase();
    var p = document.getElementById('loginPass').value;
    var found = null;
    for (var i = 0; i < USERS.length; i++) {
      if (USERS[i].user === u && USERS[i].pass === p) { found = USERS[i]; break; }
    }
    var errEl = document.getElementById('loginError');
    if (!found) { errEl.classList.add('show'); document.getElementById('loginPass').value = ''; document.getElementById('loginPass').focus(); return; }
    errEl.classList.remove('show');
    document.getElementById('loginScreen').classList.add('hidden');
    try { sessionStorage.setItem('om_role', found.role); } catch(e){}
    try { sessionStorage.setItem('om_cliente', found.cliente || ''); } catch(e){}
    try { sessionStorage.setItem('om_user', found.user || ''); } catch(e){}
    if (typeof initDashboard === 'function') initDashboard(found);
    else window._pendingSession = found;
  }
  document.getElementById('loginSubmit').addEventListener('click', tryLogin);
  document.getElementById('loginPass').addEventListener('keydown', function(e) { if (e.key === 'Enter') tryLogin(); });
  document.getElementById('loginUser').addEventListener('keydown', function(e) { if (e.key === 'Enter') document.getElementById('loginPass').focus(); });
  try {
    var role = sessionStorage.getItem('om_role');
    var cliente = sessionStorage.getItem('om_cliente') || '';
    var user = sessionStorage.getItem('om_user') || '';
    if (role) { document.getElementById('loginScreen').classList.add('hidden'); window._pendingSession = { role: role, cliente: cliente, user: user }; }
  } catch(e) {}
})();

/* ══ PWA ══ */
(function injectManifest() {
  try {
    const manifest = { name:'Painel O&M — Grid Co', short_name:'O&M Painel', start_url:window.location.pathname+window.location.search, display:'standalone', background_color:'#0E1419', theme_color:'#0E1419', icons:[{src:'data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#0E1419"/><path d="M96 30 L150 55 L150 100 C150 135 96 162 96 162 C96 162 42 135 42 100 L42 55 Z" fill="none" stroke="#F2A93B" stroke-width="8"/><circle cx="96" cy="100" r="18" fill="#F2A93B"/></svg>'), sizes:'192x192', type:'image/svg+xml', purpose:'any maskable'}] };
    const blob = new Blob([JSON.stringify(manifest)], { type:'application/json' });
    document.getElementById('manifestLink').href = URL.createObjectURL(blob);
  } catch(e) {}
})();

let _pwaPrompt = null;
try {
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); _pwaPrompt = e; if (!sessionStorage.getItem('pwa_dismissed')) document.getElementById('pwaBanner').classList.add('visible'); });
  document.getElementById('pwaInstallBtn').addEventListener('click', async () => { if (!_pwaPrompt) return; _pwaPrompt.prompt(); const { outcome } = await _pwaPrompt.userChoice; if (outcome === 'accepted') { document.getElementById('pwaBanner').classList.remove('visible'); showToast('App instalado!', true); } _pwaPrompt = null; });
  document.getElementById('pwaDismissBtn').addEventListener('click', () => { document.getElementById('pwaBanner').classList.remove('visible'); sessionStorage.setItem('pwa_dismissed','1'); });
} catch(e) {}

/* ══ TEMA ══ */
(function initTheme() {
  try { const saved = localStorage.getItem('om_theme'); const prefer = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'; applyTheme(saved || prefer); } catch(e) {}
})();
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('om_theme', t);
  const label = document.getElementById('themeLabel');
  if (label) label.textContent = t === 'light' ? '☀︎ Claro' : '☾ Escuro';
  const meta = document.getElementById('themeColorMeta');
  if (meta) meta.content = t === 'light' ? '#F0F4F8' : '#0E1419';
}
try { document.getElementById('themeToggle').addEventListener('click', () => { const current = document.documentElement.getAttribute('data-theme') || 'dark'; applyTheme(current === 'dark' ? 'light' : 'dark'); }); } catch(e) {}

/* ══ COUNTDOWN ══ */
const REFRESH_INTERVAL = 5 * 60;
let _countdownSec = REFRESH_INTERVAL;
let _countdownTimer = null;
function startCountdown() {
  clearInterval(_countdownTimer);
  _countdownSec = REFRESH_INTERVAL;
  const wrap = document.getElementById('countdownWrap');
  wrap.style.display = 'flex';
  _countdownTimer = setInterval(() => { _countdownSec--; updateCountdownUI(); if (_countdownSec <= 0) { clearInterval(_countdownTimer); fetchSheet(); } }, 1000);
  updateCountdownUI();
}
function updateCountdownUI() {
  const fill = document.getElementById('countdownFill');
  const text = document.getElementById('countdownText');
  const pct = (_countdownSec / REFRESH_INTERVAL) * 100;
  const minutes = Math.floor(_countdownSec / 60);
  const secs = _countdownSec % 60;
  if (fill) { fill.style.width = pct + '%'; fill.style.background = pct > 50 ? 'var(--teal)' : pct > 20 ? 'var(--amber)' : 'var(--red)'; }
  if (text) text.textContent = `${minutes}:${String(secs).padStart(2,'0')}`;
}

/* ══ SLA ══ */
const SLA_CONFIG = { WARN_DAYS:7, BREACH_DAYS:14, CLOSED_STATUSES:['concluído','concluido','resolvido','fechado','resolved','closed'] };

function getResponseDate(d) {
  const hist = (d.historico || '').trim();
  if (!hist) return null;
  const today = new Date(); const thisYear = today.getFullYear();
  function parseLineDate(l) {
    let m = l.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/); if (m) { const dt = new Date(+m[3],+m[2]-1,+m[1]); if (!isNaN(dt) && dt.getFullYear()>2000) return dt; }
    m = l.match(/(\d{4})-(\d{2})-(\d{2})/); if (m) { const dt = new Date(+m[1],+m[2]-1,+m[3]); if (!isNaN(dt) && dt.getFullYear()>2000) return dt; }
    m = l.match(/^(\d{1,2})\/(\d{1,2})\s*[-\u2013:]/); if (m) { const day=+m[1],month=+m[2]; if(day>=1&&day<=31&&month>=1&&month<=12){let dt=new Date(thisYear,month-1,day);if(dt>today)dt=new Date(thisYear-1,month-1,day);return dt;} }
    return null;
  }
  const lines = hist.split(/[\r\n]+/).map(l=>l.trim()).filter(Boolean);
  const dateDates = [];
  for (const line of lines) { const dt = parseLineDate(line); if (dt) { const last = dateDates[dateDates.length-1]; if (!last || dt.getTime()!==last.getTime()) dateDates.push(dt); } }
  if (dateDates.length < 2) return null;
  return dateDates[1];
}
function getResponseDays(d) {
  const statusKey = (d.status||'').toLowerCase().trim();
  if (SLA_CONFIG.CLOSED_STATUSES.some(s=>statusKey.includes(s))) return -1;
  const opened = getOpenDate(d); const response = getResponseDate(d);
  if (!opened || !response) return null;
  return Math.max(0, Math.floor((response-opened)/(1000*60*60*24)));
}
function isSlaBreached(d) {
  const statusKey = (d.status||'').toLowerCase().trim();
  if (SLA_CONFIG.CLOSED_STATUSES.some(s=>statusKey.includes(s))) return false;
  if (getResponseDate(d)) return false;
  const ageDays = getFaultAgeDays(d);
  if (ageDays===null||ageDays<0) return false;
  return ageDays >= SLA_CONFIG.BREACH_DAYS;
}
function isSlaWarning(d) {
  const statusKey = (d.status||'').toLowerCase().trim();
  if (SLA_CONFIG.CLOSED_STATUSES.some(s=>statusKey.includes(s))) return false;
  if (getResponseDate(d)) return false;
  const ageDays = getFaultAgeDays(d);
  if (ageDays===null||ageDays<0) return false;
  return ageDays >= SLA_CONFIG.WARN_DAYS && ageDays < SLA_CONFIG.BREACH_DAYS;
}
function getLastActivityDate(d) {
  const hist = (d.historico||'').trim(); if (!hist) return null;
  const today = new Date(); const thisYear = today.getFullYear();
  function parseDateFromLine(l) {
    let m = l.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/); if (m) { const dt=new Date(+m[3],+m[2]-1,+m[1]); if(!isNaN(dt)&&dt.getFullYear()>2000) return dt; }
    m = l.match(/(\d{4})-(\d{2})-(\d{2})/); if (m) { const dt=new Date(+m[1],+m[2]-1,+m[3]); if(!isNaN(dt)&&dt.getFullYear()>2000) return dt; }
    m = l.match(/^(\d{1,2})\/(\d{1,2})\s*[-\u2013:]/); if (m) { const day=+m[1],month=+m[2]; if(day>=1&&day<=31&&month>=1&&month<=12){let dt=new Date(thisYear,month-1,day);if(dt>today)dt=new Date(thisYear-1,month-1,day);return dt;} }
    return null;
  }
  const lines = hist.split(/[\r\n]+/).map(l=>l.trim()).filter(Boolean);
  let lastDate = null;
  for (const line of lines) { const dt = parseDateFromLine(line); if (dt && (!lastDate||dt>lastDate)) lastDate=dt; }
  return lastDate;
}
function getStaleDays(d) {
  const statusKey = (d.status||'').toLowerCase().trim();
  if (SLA_CONFIG.CLOSED_STATUSES.some(s=>statusKey.includes(s))) return null;
  const last = getLastActivityDate(d); if (!last) return null;
  return Math.floor((new Date()-last)/(1000*60*60*24));
}
const STALE_DAYS = 5;
function getOpenDate(d) {
  if (d.dataAbertura) { const parts=d.dataAbertura.split('/'); if(parts.length===3){const dt=new Date(+parts[2],+parts[1]-1,+parts[0]);if(!isNaN(dt)&&dt.getFullYear()>2000)return dt;} }
  const hist=(d.historico||'').trim(); if (!hist) return null;
  const today=new Date(); const thisYear=today.getFullYear();
  const lines=hist.split(/[\r\n]+/);
  for (const line of lines) {
    const l=line.trim(); if (!l) continue;
    let m=l.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/); if(m){const dt=new Date(+m[3],+m[2]-1,+m[1]);if(!isNaN(dt)&&dt.getFullYear()>2000)return dt;}
    m=l.match(/(\d{4})-(\d{2})-(\d{2})/); if(m){const dt=new Date(+m[1],+m[2]-1,+m[3]);if(!isNaN(dt)&&dt.getFullYear()>2000)return dt;}
    m=l.match(/^(\d{1,2})\/(\d{1,2})\s*[-–:]/); if(m){const day=+m[1],month=+m[2];if(day>=1&&day<=31&&month>=1&&month<=12){let dt=new Date(thisYear,month-1,day);if(dt>today)dt=new Date(thisYear-1,month-1,day);if(!isNaN(dt))return dt;}}
  }
  const mShort=hist.match(/(\d{1,2})\/(\d{1,2})\s*[-–:]/); if(mShort){const day=+mShort[1],month=+mShort[2];if(day>=1&&day<=31&&month>=1&&month<=12){let dt=new Date(thisYear,month-1,day);if(dt>today)dt=new Date(thisYear-1,month-1,day);if(!isNaN(dt))return dt;}}
  return null;
}
function getFaultAgeDays(d) {
  const statusKey=(d.status||'').toLowerCase().trim();
  if (SLA_CONFIG.CLOSED_STATUSES.some(s=>statusKey.includes(s))) return -1;
  const opened=getOpenDate(d); if (!opened) return null;
  return Math.max(0,Math.floor((new Date()-opened)/(1000*60*60*24)));
}
function getSlaClass(days) { if(days===null||days===undefined)return 'age-none'; if(days<0)return 'age-ok'; if(days>=30)return 'age-breach'; if(days>=10)return 'age-warning'; return 'age-ok'; }
function agePillText(days) { if(days===null)return 'Sem data'; if(days<0)return 'Concluído'; if(days===0)return 'Aberta hoje'; if(days===1)return 'Aberta há 1 dia'; return `Aberta há ${days} dias`; }

/* ══ DETECÇÃO DE DESLIGAMENTO ══
   Identifica ocorrências onde a usina está completamente desligada:
   - Palavras "desligada", "desligamento", "desligado" na falha/ação/causa
   - Impacto total de geração ("geração total", "geração zero", "100%")
*/
let activeDesligFilter = false;

function isDesligamento(d) {
  const statusKey = (d.status || '').toLowerCase();
  if (['concluído','concluido','resolvido','fechado'].some(s => statusKey.includes(s))) return false;

  // Analisa apenas falha e causa — não o campo ação
  const falha = (d.falha || '').toLowerCase();
  const causa = (d.causa || '').toLowerCase();
  const fc = falha + ' ' + causa;

  // Regra 1: usina/UFV + desligada/parada (próximos, em qualquer ordem)
  if (/(?:usina|ufv)\s+(?:\w+\s+){0,3}(?:desligad[ao]|parad[ao]|sem\s+energia|desenergizad[ao])/.test(fc)) return true;
  if (/(?:desligad[ao]|parad[ao])\s+(?:\w+\s+){0,3}(?:usina|ufv)/.test(fc)) return true;

  // Regra 2: desligamento + da usina/da UFV
  if (/desligamento\s+(?:total\s+)?(?:da|de)\s+(?:usina|ufv)/.test(fc)) return true;

  // Regra 3: transformador + da usina + desligado (o "da usina" é obrigatório)
  if (/transformador\s+(?:\w+\s+){0,3}(?:da|de)\s+(?:usina|ufv)\s+(?:\w+\s+){0,3}(?:desligad[ao]|parad[ao])/.test(fc)) return true;
  if (/trafo\s+(?:\w+\s+){0,3}(?:da|de)\s+(?:usina|ufv)\s+(?:\w+\s+){0,3}(?:desligad[ao]|parad[ao])/.test(fc)) return true;

  return false;
}

/* ══ SESSION ══ */
const USERS = [
  { user:'admin', pass:'gridco2026', role:'admin' },
  { user:'fredalexandrino', pass:'Fred2004@', role:'manager' },
  { user:'thopen', pass:'thopen2026', role:'client', cliente:'THOPEN' },
  { user:'renogrid', pass:'reno2026', role:'client', cliente:'RENOGRID' },
];
let _currentSession = { role:'viewer' };
let _forcedCliente = null;

function initDashboard(session) {
  _currentSession = session;
  if (session.role==='client' && session.cliente) _forcedCliente = session.cliente;
  if (session.role==='manager') {
    // Manager: acesso completo — Verificar Rondas, Chamados, Controle de Processos
    const btn = document.getElementById('processosBtn'); if (btn) btn.style.display = 'inline-flex';
    const btnR = document.getElementById('rondasBtn'); if (btnR) btnR.style.display = 'inline-flex';
    const btnC = document.getElementById('chamadosBtn'); if (btnC) btnC.style.display = 'inline-flex';
    const ab = document.getElementById('actionBar'); if (ab) ab.style.display = 'flex';
  } else if (session.role==='admin') {
    // Admin: somente visualização — sem barra de ações, sem botões de intervenção
    // (pode apenas dar refresh via botão "Atualizar dados" no header)
    const ab = document.getElementById('actionBar'); if (ab) ab.style.display = 'none';
  } else {
    // Clientes: apenas chamados
    const btnC = document.getElementById('chamadosBtn'); if (btnC) btnC.style.display = 'inline-flex';
    const ab = document.getElementById('actionBar'); if (ab) ab.style.display = 'flex';
  }
  fetchSheet();
  // Inicia push após login (apenas admin e manager)
  if (session.role === 'admin' || session.role === 'manager') {
    setTimeout(initPush, 2000);
  }
}
function canEdit() { return _currentSession.role==='manager'; } // admin: só visualização
/* ══ CONFIG ══ */
const SHEET_ID = '1VLo8__wxSJVWiUIFd_JTcOnadJlUt440i1M1pC0ehTs';
const GID = '0';
const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbya1PLWxm1quM889etDastzC4BMUOQCy6wYVZfWTFY5jy9jLKQR32XJdco7ywPaxmVm3g/exec';

/* ══ SNAPSHOT ══ */
const SNAPSHOT = [
  {id:"1",dataAbertura:"17/06/2026",cliente:"THOPEN",usina:"Matão I",equipamento:"Tracker 12",falha:"Falha no Tracker 12",causa:"TCU com defeito na bateria",impactados:"Tracker 12, TCU",acao:"Realizar reset e avaliar abertura de chamado para bateria",status:"Em Aberto",historico:"17/06/2026 14:00 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"2",dataAbertura:"17/06/2026",cliente:"THOPEN",usina:"Matão I",equipamento:"EP4",falha:"Falha de comunicação do EP4",causa:"Igate com problema",impactados:"EP4, Igate",acao:"Verificar integridade do Igate",status:"Em Aberto",historico:"17/06/2026 14:00 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"3",dataAbertura:"17/06/2026",cliente:"THOPEN",usina:"Topázio",equipamento:"Nobreak G3 mini",falha:"Falha de comunicação do nobreak G3 mini",causa:"Sem comunicação com o Igate",impactados:"Nobreak G3 mini, Igate",acao:"Investigar falha específica",status:"Em Aberto",historico:"17/06/2026 14:00 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"4",dataAbertura:"17/06/2026",cliente:"THOPEN",usina:"Sítio Bonfim",equipamento:"Exaustores",falha:"Parada de funcionamento",causa:"Falha interna no motor",impactados:"Exaustores e Componentes do QGBT",acao:"Deve ser realizada a compra",status:"Em Aberto",historico:"17/06/2026 15:26 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"5",dataAbertura:"17/06/2026",cliente:"THOPEN",usina:"Sítio Bonfim",equipamento:"Câmeras (CFTV)",falha:"Câmeras pararam de funcionar",causa:"Água que entrou no conector RJ45",impactados:"Câmeras (15% do monitoramento)",acao:"Esperando autorização para troca do conector RJ45",status:"Em Aberto",historico:"17/06/2026 15:26 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"6",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Piranometro POA",falha:"Equipamento parou de enviar informações",causa:"Defeito interno",impactados:"Estação solarimétrica",acao:"Enviado para reparo na fabricante (Romiotto)",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"7",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Piranometro POARI",falha:"Equipamento parou de enviar informações",causa:"Defeito interno",impactados:"Estação solarimétrica",acao:"Enviado para reparo na fabricante (Romiotto)",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"8",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Motor Tracker 08",falha:"Equipamento não estava funcionando",causa:"Água dentro do motor",impactados:"Tracker 08",acao:"Manutenção realizada no motor. Retirada toda a água, passado limpa contato.",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"9",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Motor Tracker 19",falha:"Equipamento não estava funcionando",causa:"Água dentro do motor",impactados:"Tracker 19",acao:"Manutenção realizada no motor. Retirada toda a água, passado limpa contato.",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"10",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Motor Tracker 29",falha:"Equipamento não estava funcionando",causa:"Água dentro do motor",impactados:"Tracker 29",acao:"Manutenção realizada no motor. Retirada toda a água, passado limpa contato.",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
  {id:"11",dataAbertura:"18/06/2026",cliente:"THOPEN",usina:"Poconé",equipamento:"Motor/TCU Tracker 48",falha:"Motor sem força para girar. TCU não respondia",causa:"Água dentro do motor. TCU com problema de conexão",impactados:"Tracker 48",acao:"Manutenção no motor. Garantia acionada.",status:"Em Aberto",historico:"18/06/2026 09:20 - Registro inicial.",ticketFabricante:"",numeroOS:""},
];

/* ══ ESTADO ══ */
let DATA = []; let filtered = []; let sortKey = 'id';
let activeCausa = null; let activeSlaFilter = false;

// ── ABAS: 'ativas' | 'historico' ──────────────────────────────────────────
let activeTab = 'ativas';
const STATUS_CONCLUIDO = ['concluído','concluido','resolvido','fechado','resolved','closed'];
function isConcluido(d) { const s=(d.status||'').toLowerCase(); return STATUS_CONCLUIDO.some(x=>s.includes(x)); }

/* ══ FETCH ══ */
async function fetchSheet() {
  const btn = document.getElementById('updateBtn');
  const badge = document.getElementById('sourceBadge');
  const label = document.getElementById('sourceLabel');
  clearInterval(_countdownTimer);
  document.getElementById('countdownWrap').style.display = 'none';
  btn.disabled = true; btn.classList.add('spinning');
  badge.className = 'source-badge loading'; label.textContent = 'buscando dados…';
  showSkeletons();
  let parsed = null;
  const CLEAR_URL = 'https://script.google.com/macros/s/AKfycbyiI0LvyPPa6nzLYOk2CiwyrtZS5yBtFBei-gD_pywBh5JYxL74vGx759lZ6jj26Hd4/exec';
  fetch(CLEAR_URL, { method:'GET', mode:'no-cors' }).catch(() => {});

  try {
    const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/gviz/tq?tqx=out:json&gid=${GID}&headers=1&tq=select%20*&t=${Date.now()}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const raw = await res.text();
    const jsonStr = raw.replace(/^[^(]*\(/, '').replace(/\);?\s*$/, '');
    const json = JSON.parse(jsonStr);
    const rows = json.table.rows;
    const cols = json.table.cols;
    const idx = {};
    cols.forEach((c, i) => {
      const lbl = (c.label || '').toLowerCase().trim();
      if (lbl === 'id') idx.id = i;
      else if (lbl === 'cliente') idx.cliente = i;
      else if (lbl === 'usina') idx.usina = i;
      else if (lbl === 'equipamento') idx.equipamento = i;
      else if (lbl.startsWith('falha')) idx.falha = i;
      else if (lbl.startsWith('causa')) idx.causa = i;
      else if (lbl.includes('impactado')) idx.impactados = i;
      else if (lbl.startsWith('ação') || lbl.startsWith('acao')) idx.acao = i;
      else if (lbl.includes('status')) idx.status = i;
      else if (lbl.includes('histórico') || lbl.includes('historico') || lbl.includes('hist') || lbl.includes('timeline') || lbl.includes('registro')) idx.historico = i;
      else if (lbl.includes('ticket')) idx.ticket = i;
      else if (lbl.includes('os') || lbl.includes('ordem')) idx.os = i;
      else if (lbl.includes('data') && lbl.includes('abertura')) idx.dataAbertura = i;
      else if (lbl === 'data_abertura' || lbl === 'dataabertura') idx.dataAbertura = i;
    });
    const g = (r, i) => { const cell = r.c?.[i]; if (!cell) return ''; const v = cell.v != null ? String(cell.v) : ''; const f = cell.f != null ? String(cell.f) : ''; return (f.length > v.length ? f : v).trim(); };
    parsed = rows.map(r => ({
      id: g(r, idx.id??0), dataAbertura: idx.dataAbertura!=null?g(r,idx.dataAbertura):'',
      cliente: g(r, idx.cliente??1), usina: normalizeUsina(g(r, idx.usina??2)),
      equipamento: g(r, idx.equipamento??3), falha: g(r, idx.falha??4),
      causa: g(r, idx.causa??5), impactados: g(r, idx.impactados??6),
      acao: g(r, idx.acao??7), status: g(r, idx.status??8) || 'Em Aberto',
      historico: g(r, idx.historico??11),
      ticketFabricante: idx.ticket!=null?g(r,idx.ticket):'', numeroOS: idx.os!=null?g(r,idx.os):g(r,10),
    })).filter(d => d.equipamento && d.id);
    if (parsed.length === 0) throw new Error('gviz retornou 0 registros');
  } catch(e1) {
    try {
      const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=csv&gid=${GID}&t=${Date.now()}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const csv = await res.text();
      const rows = splitCSVRows(csv);
      if (rows.length < 2) throw new Error('CSV vazio');
      const headers = parseCSVLine(rows[0]).map(h => h.toLowerCase().trim());
      const idx = {};
      headers.forEach((h, i) => {
        if (h === 'id') idx.id = i;
        else if (h === 'cliente') idx.cliente = i;
        else if (h === 'usina') idx.usina = i;
        else if (h === 'equipamento') idx.equipamento = i;
        else if (h.startsWith('falha')) idx.falha = i;
        else if (h.startsWith('causa')) idx.causa = i;
        else if (h.includes('impactado')) idx.impactados = i;
        else if (h.startsWith('ação') || h.startsWith('acao')) idx.acao = i;
        else if (h.includes('status')) idx.status = i;
        else if (h.includes('histórico') || h.includes('historico') || h.includes('hist') || h.includes('timeline') || h.includes('registro')) idx.historico = i;
        else if (h.includes('ticket')) idx.ticket = i;
        else if (h.includes('os') || h.includes('ordem')) idx.os = i;
        else if (h.includes('data') && h.includes('abertura')) idx.dataAbertura = i;
        else if (h === 'data_abertura' || h === 'dataabertura') idx.dataAbertura = i;
      });
      parsed = rows.slice(1).filter(r=>r.trim()).map(r => {
        const cells = parseCSVLine(r);
        const g = (i) => (cells[i] || '').trim();
        return { id:g(idx.id??0), dataAbertura:idx.dataAbertura!=null?g(idx.dataAbertura):'', cliente:g(idx.cliente??1), usina:normalizeUsina(g(idx.usina??2)), equipamento:g(idx.equipamento??3), falha:g(idx.falha??4), causa:g(idx.causa??5), impactados:g(idx.impactados??6), acao:g(idx.acao??7), status:g(idx.status??8)||'Em Aberto', historico:g(idx.historico??11), ticketFabricante:idx.ticket!=null?g(idx.ticket):'', numeroOS:idx.os!=null?g(idx.os):g(10) };
      }).filter(d => d.equipamento && d.id);
      if (parsed.length === 0) throw new Error('CSV 0 registros');
    } catch(e2) { parsed = null; }
  }

  if (parsed && parsed.length > 0) {
    DATA = parsed;
    const now = new Date();
    const ts = now.toLocaleDateString('pt-BR') + ' ' + now.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
    badge.className = 'source-badge live'; label.textContent = `ao vivo · ${ts}`;
    showToast(`${DATA.length} registros carregados`, true);
  } else {
    DATA = SNAPSHOT.slice();
    badge.className = 'source-badge error'; label.textContent = 'snapshot local · 18/06/2026';
    showToast('Usando dados locais (sem conexão com planilha)', false);
  }
  btn.disabled = false; btn.classList.remove('spinning');
  populateFilters();
  const clienteParam = _forcedCliente || URL_CLIENTE;
  if (clienteParam) activateClientMode(clienteParam);
  clearFilters();
  startCountdown();
}

/* ══ CSV PARSERS ══ */
function parseCSVLine(line) { const result=[]; let current='',inQuotes=false; for(let i=0;i<line.length;i++){const char=line[i],next=line[i+1]; if(char==='"'){if(inQuotes&&next==='"'){current+='"';i++;}else inQuotes=!inQuotes;}else if(char===','&&!inQuotes){result.push(current);current='';}else{current+=char;}} result.push(current); return result; }
function splitCSVRows(csv) { const rows=[]; let current='',inQuotes=false; for(let i=0;i<csv.length;i++){const ch=csv[i]; if(ch==='"'){if(inQuotes&&csv[i+1]==='"'){current+='"';i++;}else inQuotes=!inQuotes;current+=ch;}else if((ch==='\n'||ch==='\r')&&!inQuotes){if(ch==='\r'&&csv[i+1]==='\n')i++;if(current.trim())rows.push(current);current='';}else{current+=ch;}} if(current.trim())rows.push(current); return rows; }

/* ══ UTILS ══ */
function esc(s) { return (s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

function normalizeUsina(raw) {
  if (!raw) return raw;
  const s = raw.trim();
  const BES = /^boa\s+espe[rn]an[çc]a\s+do\s+sul\s*/i;
  if (BES.test(s)) { const suf = s.replace(BES,'').trim().toUpperCase(); if(/^(1A?|IA?|A)$/.test(suf)||suf==='1'||suf==='I') return 'Boa Esperança do Sul 1'; if(/^(2B?|IIB?|IB|B)$/.test(suf)||suf==='2'||suf==='II') return 'Boa Esperança do Sul 2'; return 'Boa Esperança do Sul'+(suf?' '+suf:''); }
  const GENERIC = /^(.+?)\s+(1|2|i|ii|a|b)$/i;
  const gm = s.match(GENERIC);
  if (gm) { const base=gm[1].trim(); const suf=gm[2].toUpperCase(); const toRoman={'1':'I','I':'I','A':'I','2':'II','II':'II','B':'II'}; const roman=toRoman[suf]; if(roman) return base+' '+roman; }
  return s;
}

const STATUS_PALETTE = { 'em aberto':{color:'#E2543D',glow:'rgba(226,84,61,.55)'}, 'aguardando cliente':{color:'#A855F7',glow:'rgba(168,85,247,.55)'}, 'aguardando fabricante':{color:'#FB923C',glow:'rgba(251,146,60,.55)'}, 'em andamento':{color:'#EAB308',glow:'rgba(234,179,8,.55)'}, 'concluído':{color:'#22C55E',glow:'rgba(34,197,94,.55)'}, 'concluido':{color:'#22C55E',glow:'rgba(34,197,94,.55)'}, 'resolvido':{color:'#10B981',glow:'rgba(16,185,129,.55)'}, 'fechado':{color:'#6B7780',glow:'rgba(107,119,128,.55)'} };
const STATUS_AUTO = [{color:'#3FC1B0',glow:'rgba(63,193,176,.55)'},{color:'#60A5FA',glow:'rgba(96,165,250,.55)'},{color:'#F472B6',glow:'rgba(244,114,182,.55)'}];
const _statusColorCache = {}; let _statusAutoIdx = 0;
function getStatusPalette(s) { const key=(s||'').toLowerCase().trim(); if(STATUS_PALETTE[key])return STATUS_PALETTE[key]; for(const[k,v]of Object.entries(STATUS_PALETTE)){if(key.includes(k)||k.includes(key))return v;} if(!_statusColorCache[key]){_statusColorCache[key]=STATUS_AUTO[_statusAutoIdx%STATUS_AUTO.length];_statusAutoIdx++;} return _statusColorCache[key]; }
function statusColor(s) { return getStatusPalette(s).color; }
function statusGlow(s) { return getStatusPalette(s).glow; }
function statusBg(s) { return getStatusPalette(s).color+'1A'; }
function statusBorder(s) { return getStatusPalette(s).color+'44'; }

const CLIENT_PALETTE = { 'THOPEN':{color:'#5B9BD5',glow:'rgba(91,155,213,.55)'}, 'RENOGRID':{color:'#F2A93B',glow:'rgba(242,169,59,.55)'} };
const AUTO_COLORS = [{color:'#3FC1B0',glow:'rgba(63,193,176,.55)'},{color:'#C084FC',glow:'rgba(192,132,252,.55)'},{color:'#F87171',glow:'rgba(248,113,113,.55)'},{color:'#34D399',glow:'rgba(52,211,153,.55)'},{color:'#FB923C',glow:'rgba(251,146,60,.55)'}];
const _clientColorCache = {}; let _autoIdx = 0;
function clientColor(cliente) { const key=(cliente||'').toUpperCase().trim(); if(CLIENT_PALETTE[key])return CLIENT_PALETTE[key]; if(!_clientColorCache[key]){_clientColorCache[key]={...AUTO_COLORS[_autoIdx%AUTO_COLORS.length],label:cliente};_autoIdx++;} return _clientColorCache[key]; }

function isValidTicket(t) { const v=(t||'').trim().toUpperCase(); return v!==''&&v!=='N/A'&&v!=='NA'&&v!=='-'&&v!=='S/N'&&v!=='SN'; }

function categorizeCausa(c) {
  const v=(c||'').toLowerCase().trim(); if(!v)return 'Não informado';
  if(v.includes('água')||v.includes('agua')||v.includes('umidade')||v.includes('rj45'))return 'Infiltração de água';
  if(v.includes('igate')||v.includes('comunicaç')||v.includes('fieldlogger')||v.includes('sem comunicar'))return 'Falha de comunicação';
  if(v.includes('bateria')||v.includes('tensão')||v.includes('nobreak')||v.includes('viciada'))return 'Problema de alimentação';
  if(v.includes('motor')||v.includes('atuador'))return 'Falha no motor';
  if(v.includes('tcu')||v.includes('controlador'))return 'Falha no TCU / controlador';
  if(v.includes('conector')||v.includes('cabo'))return 'Problema em conector / cabo';
  if(v.includes('sensor')||v.includes('piranô')||v.includes('pirano')||v.includes('poa'))return 'Falha em sensor / medição';
  if(v.includes('exaustor')||v.includes('ventil'))return 'Falha mecânica';
  if(v.includes('firmware')||v.includes('software')||v.includes('configur'))return 'Software / configuração';
  if(v.includes('defeito')||v.includes('interno'))return 'Defeito interno';
  if(v.includes('garantia')||v.includes('romiotto'))return 'Acionamento de garantia';
  if(v.includes('inversor'))return 'Falha em inversor';
  return (v.split(/\s+/).slice(0,4).join(' ')||'').replace(/^\w/,c=>c.toUpperCase());
}

function iconFor(e) {
  const v=e.toLowerCase();
  if(v.includes('câmera')||v.includes('camera')||v.includes('cftv'))return `<svg viewBox="0 0 24 24"><path d="M4 7h3l1.6-2h6.8L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.2"/></svg>`;
  if(v.includes('exaustor'))return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="1.6"/><path d="M12 12c0-3 1.6-5.5 4-5.5s1.8 3 0 4.5C13.5 12 12 12 12 12zM12 12c-3 0-5.5-1.6-5.5-4s3-1.8 4.5 0C12 9.5 12 11 12 12zM12 12c0 3-1.6 5.5-4 5.5s-1.8-3 0-4.5C10.5 12 12 12 12 12zM12 12c3 0 5.5 1.6 5.5 4s-3 1.8-4.5 0C12 14.5 12 13 12 12z"/></svg>`;
  if(v.includes('tracker'))return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7.5"/><path d="M12 4.5v3M12 16.5v3M4.5 12h3M16.5 12h3"/><circle cx="12" cy="12" r="2.5"/></svg>`;
  if(v.includes('nobreak'))return `<svg viewBox="0 0 24 24"><path d="M13 3 5 14h6l-1 7 8-11h-6z"/></svg>`;
  if(v.includes('ep4')||v.includes('igate'))return `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="1.4"/><path d="M8.5 15.5a5 5 0 0 1 0-7M15.5 15.5a5 5 0 0 0 0-7M5.5 18.5a9 9 0 0 1 0-13M18.5 18.5a9 9 0 0 0 0-13"/></svg>`;
  if(v.includes('piranô')||v.includes('pirano'))return `<svg viewBox="0 0 24 24"><path d="M4 17a8 8 0 0 1 16 0"/><line x1="12" y1="17" x2="12" y2="9"/><line x1="12" y1="9" x2="14.5" y2="11.2"/><circle cx="12" cy="17" r="1.3"/></svg>`;
  return `<svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5.6 5.6L3 18l3 3 6.1-6.1a4 4 0 0 0 5.6-5.6l-2.3 2.3-2-2z"/></svg>`;
}

function parseHistorico(text) {
  if (!text) return [];
  return text.trim().split(/\r\n|\r|\n/).map(line => {
    const m = line.match(/^(\d{2}\/\d{2}\/\d{4}\s+\d{2}:\d{2})\s*-\s*([\s\S]*)$/);
    return m ? {date:m[1],text:m[2].trim()} : {date:'',text:line.trim()};
  }).filter(e=>e.text);
}

/* ══ TOAST ══ */
function showToast(msg, ok=true) {
  const t=document.getElementById('toast'); const ti=document.getElementById('toastIcon'); const tm=document.getElementById('toastMsg');
  t.className='toast'+(ok?'':' err');
  ti.innerHTML=ok?`<polyline points="20 6 9 17 4 12"/>`:`<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>`;
  tm.textContent=msg; t.classList.add('show'); clearTimeout(t._t); t._t=setTimeout(()=>t.classList.remove('show'),4000);
}

/* ══ SKELETON ══ */
function showSkeletons() { document.getElementById('cardGrid').innerHTML=Array(6).fill(0).map(()=>`<div class="skel-card"><div class="skeleton skel-line" style="width:40%;margin-bottom:14px"></div><div class="skeleton skel-title"></div><div class="skeleton skel-sub"></div><div class="skeleton skel-foot"></div></div>`).join(''); }

/* ══ MULTI-SELECT ══ */
const selCliente=new Set(); const selUsina=new Set(); const selStatus=new Set();
function buildMultiSelect(cfg) {
  const drop=document.getElementById(cfg.dropId); const trigger=document.getElementById(cfg.triggerId); const textEl=document.getElementById(cfg.textId);
  function updateTrigger() { const n=cfg.set.size; textEl.innerHTML=''; if(n===0){textEl.textContent=cfg.allLabel;textEl.classList.remove('active');trigger.querySelector('.ms-badge')?.remove();}else{const first=[...cfg.set][0];textEl.textContent=n===1?first:`${first} +${n-1}`;textEl.classList.add('active');let badge=trigger.querySelector('.ms-badge');if(!badge){badge=document.createElement('span');badge.className='ms-badge';trigger.insertBefore(badge,trigger.querySelector('.ms-arrow'));}badge.textContent=n;} }
  function renderDrop(options) {
    const allChecked=cfg.set.size===0;
    const todosChecked=options.every(v=>cfg.set.has(v));
    // Botão "Desmarcar todos" — aparece quando há itens selecionados
    const desmarcarBtn = cfg.set.size > 0
      ? `<div class="ms-deselect-all" id="${cfg.dropId}-deselect">✕ Desmarcar todos</div>`
      : '';
    drop.innerHTML=desmarcarBtn+
      `<div class="ms-option all-opt${allChecked||todosChecked?' checked':''}"><div class="ms-check"><svg viewBox="0 0 12 9"><polyline points="1 4 4.5 8 11 1"/></svg></div><span>${cfg.allLabel}</span></div>`+
      options.map(v=>`<div class="ms-option${cfg.set.has(v)?' checked':''}" data-val="${esc(v)}"><div class="ms-check"><svg viewBox="0 0 12 9"><polyline points="1 4 4.5 8 11 1"/></svg></div><span>${esc(v)}</span></div>`).join('');
    // "Todas/Todos" — marca TODOS os itens individualmente
    drop.querySelector('.all-opt').addEventListener('click',e=>{
      e.stopPropagation();
      if(todosChecked){
        // Todos marcados individualmente → desmarca tudo (volta ao padrão)
        cfg.set.clear();
      } else {
        // Estado padrão (vazio) ou parcialmente marcado → marca todos individualmente
        options.forEach(v=>cfg.set.add(v));
      }
      updateTrigger();renderDrop(options);cfg.onchange();
    });
    // Botão "Desmarcar todos"
    const desBtn=drop.querySelector(`#${cfg.dropId}-deselect`);
    if(desBtn) desBtn.addEventListener('click',e=>{e.stopPropagation();cfg.set.clear();updateTrigger();renderDrop(options);cfg.onchange();});
    // Opções individuais
    drop.querySelectorAll('.ms-option[data-val]').forEach(opt=>{opt.addEventListener('click',e=>{e.stopPropagation();const v=opt.dataset.val;cfg.set.has(v)?cfg.set.delete(v):cfg.set.add(v);updateTrigger();renderDrop(options);cfg.onchange();});});
  }
  trigger.addEventListener('click',e=>{e.stopPropagation();const isOpen=drop.classList.contains('open');closeAllDropdowns();if(!isOpen){drop.classList.add('open');trigger.classList.add('open');}});
  return {renderDrop,updateTrigger};
}
const msCliente=buildMultiSelect({dropId:'ms-cliente-drop',triggerId:'ms-cliente-trigger',textId:'ms-cliente-text',set:selCliente,allLabel:'Todos',onchange:applyFilters});
const msUsina=buildMultiSelect({dropId:'ms-usina-drop',triggerId:'ms-usina-trigger',textId:'ms-usina-text',set:selUsina,allLabel:'Todas',onchange:applyFilters});
const msStatus=buildMultiSelect({dropId:'ms-status-drop',triggerId:'ms-status-trigger',textId:'ms-status-text',set:selStatus,allLabel:'Todos',onchange:applyFilters});
function closeAllDropdowns() { document.querySelectorAll('.ms-dropdown').forEach(d=>d.classList.remove('open')); document.querySelectorAll('.ms-trigger').forEach(t=>t.classList.remove('open')); }
document.addEventListener('click', closeAllDropdowns);

/* ══ FILTROS ══ */
function populateFilters() {
  const clientes=[...new Set(DATA.map(d=>d.cliente))].sort();
  const usinas=[...new Set(DATA.map(d=>d.usina))].sort();
  const statuses=[...new Set(DATA.map(d=>d.status))].sort();
  msCliente.renderDrop(clientes); msUsina.renderDrop(usinas); msStatus.renderDrop(statuses);
  msCliente.updateTrigger(); msUsina.updateTrigger(); msStatus.updateTrigger();
}
function applyFilters() {
  const q=document.getElementById('fSearch').value.trim().toLowerCase();
  const dateFrom=document.getElementById('fDateFrom').value;
  const dateTo=document.getElementById('fDateTo').value;
  // Filtra por aba (Ativas vs Histórico) — aplicado ANTES dos outros filtros
  const dataAtivas    = DATA.filter(d => !isConcluido(d));
  const dataHistorico = DATA.filter(d =>  isConcluido(d));
  const dataBase      = activeTab === 'historico' ? dataHistorico : dataAtivas;

  // Atualiza contadores das abas
  const tA = document.getElementById('tabAtivasCount');
  const tH = document.getElementById('tabHistoricoCount');
  if (tA) tA.textContent = dataAtivas.length;
  if (tH) tH.textContent = dataHistorico.length;

  // Banner histórico
  const hBanner = document.getElementById('historicoBanner');
  const hCount  = document.getElementById('historicoCount');
  if (hBanner) {
    if (activeTab === 'historico') {
      hBanner.classList.add('visible');
      if (hCount) hCount.textContent = dataHistorico.length;
    } else {
      hBanner.classList.remove('visible');
    }
  }

  filtered=dataBase.filter(d=>{
    if(selCliente.size>0&&!selCliente.has(d.cliente))return false;
    if(selUsina.size>0&&!selUsina.has(d.usina))return false;
    if(selStatus.size>0&&!selStatus.has(d.status))return false;
    if(q){const hay=(d.equipamento+' '+d.falha+' '+d.causa+' '+d.impactados+' '+d.usina+' '+d.ticketFabricante+' '+d.numeroOS).toLowerCase();if(!hay.includes(q))return false;}
    if(activeCausa&&categorizeCausa(d.causa)!==activeCausa)return false;
    if(activeSlaFilter&&!isSlaBreached(d))return false;
    if(activeDesligFilter&&!isDesligamento(d))return false;
    if(dateFrom||dateTo){const opened=getOpenDate(d);if(!opened)return!dateFrom;const op=opened.getTime();if(dateFrom){const[yf,mf,df]=dateFrom.split('-').map(Number);if(op<new Date(yf,mf-1,df).getTime())return false;}if(dateTo){const[yt,mt,dt]=dateTo.split('-').map(Number);if(op>=new Date(yt,mt-1,dt+1).getTime())return false;}}
    return true;
  });
  applySortAndRender();
}
function clearFilters() {
  selCliente.clear();selUsina.clear();selStatus.clear();
  msCliente.updateTrigger();msUsina.updateTrigger();msStatus.updateTrigger();
  document.getElementById('fSearch').value='';
  document.getElementById('fDateFrom').value='';
  document.getElementById('fDateTo').value='';
  activeCausa=null;activeSlaFilter=false;activeDesligFilter=false;
  document.getElementById('slaBanner').classList.remove('active-filter');
  document.getElementById('desligBanner').classList.remove('active-filter');
  populateFilters();applyFilters();
}

/* ══ SORT & RENDER ══ */
function applySortAndRender() {
  // Desligamentos sempre sobem para o topo, independente da ordenação
  const sorted=[...filtered].sort((a,b)=>{
    const aDeslig=isDesligamento(a)?1:0, bDeslig=isDesligamento(b)?1:0;
    if(aDeslig!==bDeslig) return bDeslig-aDeslig;
    if(sortKey==='id')return +a.id-+b.id;
    // "Registradas recentemente" = maior ID primeiro (última linha da planilha = mais novo)
    if(sortKey==='newest')return +b.id-+a.id;
    if(sortKey==='usina')return a.usina.localeCompare(b.usina);
    if(sortKey==='equip')return a.equipamento.localeCompare(b.equipamento);
    if(sortKey==='age'){const da=getFaultAgeDays(a)??-999;const db=getFaultAgeDays(b)??-999;return db-da;}
    if(sortKey==='recent'){const da2=getOpenDate(a);const db2=getOpenDate(b);const ta=da2?da2.getTime():0;const tb=db2?db2.getTime():0;return tb-ta;}
    return 0;
  });
  document.getElementById('totalBadge').textContent=`${DATA.length} registros na planilha`;
  const breachList=filtered.filter(d=>isSlaBreached(d));
  const slaBanner=document.getElementById('slaBanner');
  if(breachList.length>0){slaBanner.classList.add('visible');document.getElementById('slaBannerText').textContent=activeSlaFilter?`Filtrando ${breachList.length} falha${breachList.length>1?'s':''} com SLA vencido — sem resposta da equipe há mais de ${SLA_CONFIG.BREACH_DAYS} dias · clique para limpar`:`${breachList.length} falha${breachList.length>1?'s':''} com SLA vencido — abertas há mais de ${SLA_CONFIG.BREACH_DAYS} dias sem resposta da equipe de campo`;}
  else{slaBanner.classList.remove('visible');if(activeSlaFilter){activeSlaFilter=false;slaBanner.classList.remove('active-filter');}}
  const clients=[...new Set(DATA.map(d=>d.cliente))].sort();
  const legend=document.getElementById('clientLegend');
  legend.innerHTML=`<span class="legend-label">Clientes:</span>`+clients.map(c=>{const cc=clientColor(c);const isSel=selCliente.has(c);const isDim=selCliente.size>0&&!isSel;return`<span class="legend-item${isSel?' selected':''}${isDim?' dimmed':''}" data-cliente="${esc(c)}" style="color:${cc.color};background:${cc.color}${isSel?'28':'14'};border-color:${cc.color}${isSel?'66':'33'};"><span class="legend-dot" style="background:${cc.color}"></span>${esc(c)}</span>`;}).join('');
  legend.querySelectorAll('.legend-item').forEach(el=>{el.addEventListener('click',()=>{const c=el.dataset.cliente;selCliente.has(c)?selCliente.delete(c):selCliente.add(c);msCliente.updateTrigger();msCliente.renderDrop(clients);applyFilters();});});
  const comTicket=filtered.filter(d=>isValidTicket(d.ticketFabricante)).length;
  const badge=document.getElementById('chamadosBadge');if(badge)badge.textContent=comTicket;
  renderKPIs();renderBreakdowns();
  _lastSorted=sorted;
  if(viewMode==='table'){document.getElementById('cardGrid').style.display='none';document.getElementById('tableWrap').style.display='';renderTable(sorted);}
  else{document.getElementById('cardGrid').style.display='';document.getElementById('tableWrap').style.display='none';renderCards(sorted);}
}

/* ══ KPIs ══ */
function renderKPIs() {
  const tot=filtered.length;
  const abertos=filtered.filter(d=>d.status.toLowerCase().includes('aberto')).length;
  const slaBreach=filtered.filter(d=>isSlaBreached(d)).length;
  const staleCount=filtered.filter(d=>{const s=getStaleDays(d);return s!==null&&s>=STALE_DAYS;}).length;
  const desligCount=filtered.filter(d=>isDesligamento(d)).length;

  document.getElementById('kpis').innerHTML=[
    {num:tot,       label:'Falhas filtradas',              color:'var(--amber)', glow:'rgba(242,169,59,.08)', icon:`<svg viewBox="0 0 24 24"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`, cls:''},
    {num:abertos,   label:'Em aberto',                     color:'var(--red)',   glow:'rgba(226,84,61,.08)',  icon:`<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>`, cls:''},
    {num:slaBreach, label:`SLA vencido (+${SLA_CONFIG.BREACH_DAYS}d)`, color:'var(--red)', glow:'rgba(226,84,61,.1)', icon:`<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`, cls:''},
    {num:desligCount,label:'Usinas desligadas',            color:'#FF4444',      glow:'rgba(255,68,68,.12)',  icon:`<svg viewBox="0 0 24 24"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>`, cls:'kpi-deslig'},
  ].map(k=>`<div class="kpi ${k.cls}" style="--kpi-color:${k.color};--kpi-glow:${k.glow}" ${k.cls?'id="kpiDeslig" title="Clique para filtrar desligamentos"':''}><div class="num">${String(k.num).padStart(2,'0')}</div><div class="label">${k.label}</div><div class="kicon">${k.icon}</div></div>`).join('');

  // Atualiza banner de desligamentos
  const banner = document.getElementById('desligBanner');
  const desligamentos = filtered.filter(d=>isDesligamento(d));
  if (desligamentos.length > 0) {
    banner.classList.add('visible');
    document.getElementById('desligCount').textContent = desligamentos.length;
    const usinas = [...new Set(desligamentos.map(d=>d.usina))];
    document.getElementById('desligTitle').textContent =
      desligamentos.length === 1
        ? `⚡ ${usinas[0].toUpperCase()} — DESLIGADA`
        : `⚡ ${desligamentos.length} USINAS DESLIGADAS`;
    document.getElementById('desligSub').textContent =
      usinas.slice(0,3).join(' · ') + (usinas.length > 3 ? ` +${usinas.length-3}` : '');
  } else {
    banner.classList.remove('visible');
    if (activeDesligFilter) { activeDesligFilter = false; banner.classList.remove('active-filter'); }
  }

  // Listener no KPI de desligamentos
  const kpiDeslig = document.getElementById('kpiDeslig');
  if (kpiDeslig) kpiDeslig.addEventListener('click', toggleDesligFilter);
}

function toggleDesligFilter() {
  activeDesligFilter = !activeDesligFilter;
  const banner = document.getElementById('desligBanner');
  banner.classList.toggle('active-filter', activeDesligFilter);
  if (activeDesligFilter) window.scrollTo({top:0, behavior:'smooth'});
  applyFilters();
}

/* ══ BREAKDOWNS ══ */
function makeBar(id, entries, totalId) {
  const max=Math.max(1,...entries.map(e=>e[1]));
  document.getElementById(totalId).textContent=`${entries.length} tipo${entries.length!==1?'s':''}`;
  document.getElementById(id).innerHTML=entries.length===0?`<div style="color:var(--text-faint);font-size:12px;">Sem dados.</div>`:entries.map(([label,count,color])=>`<div class="bar-row" data-group="${esc(label)}"><div class="bar-label" title="${esc(label)}">${esc(label)}</div><div class="bar-track"><div class="bar-fill" style="width:${(count/max)*100}%;background:${color||'var(--amber)'}"></div></div><div class="bar-count">${count}</div></div>`).join('');
}
function renderBreakdowns() {
  const uMap={},sMap={},cMap={},usinaClient={};
  // Painel "Falhas por usina" conta apenas ATIVAS (independente da aba selecionada)
  const filteredAtivas = activeTab === 'historico'
    ? DATA.filter(d => !isConcluido(d))
    : filtered;
  filteredAtivas.forEach(d=>{uMap[d.usina]=(uMap[d.usina]||0)+1;usinaClient[d.usina]=d.cliente;});
  filtered.forEach(d=>{sMap[d.status]=(sMap[d.status]||0)+1;const c=categorizeCausa(d.causa);cMap[c]=(cMap[c]||0)+1;});
  makeBar('usinaBars',Object.entries(uMap).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[k,v,clientColor(usinaClient[k]).color]),'usinaTotal');
  makeBar('statusBars',Object.entries(sMap).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[k,v,statusColor(k)]),'statusTotal');
  makeBar('causaBars',Object.entries(cMap).sort((a,b)=>b[1]-a[1]).map(([k,v])=>[k,v,'var(--blue)']),'causaTotal');
  document.querySelectorAll('#usinaBars .bar-row').forEach(r=>r.addEventListener('click',()=>{const v=r.dataset.group;selUsina.has(v)?selUsina.delete(v):selUsina.add(v);msUsina.updateTrigger();msUsina.renderDrop([...new Set(DATA.map(d=>d.usina))].sort());applyFilters();}));
  document.querySelectorAll('#statusBars .bar-row').forEach(r=>r.addEventListener('click',()=>{const v=r.dataset.group;selStatus.has(v)?selStatus.delete(v):selStatus.add(v);msStatus.updateTrigger();msStatus.renderDrop([...new Set(DATA.map(d=>d.status))].sort());applyFilters();}));
  document.querySelectorAll('#causaBars .bar-row').forEach(r=>r.addEventListener('click',()=>{activeCausa=activeCausa===r.dataset.group?null:r.dataset.group;applyFilters();}));
  document.querySelectorAll('#usinaBars .bar-row').forEach(r=>r.classList.toggle('selected',selUsina.has(r.dataset.group)));
  document.querySelectorAll('#statusBars .bar-row').forEach(r=>r.classList.toggle('selected',selStatus.has(r.dataset.group)));
  document.querySelectorAll('#causaBars .bar-row').forEach(r=>r.classList.toggle('selected',r.dataset.group===activeCausa));
}

/* ══ CARDS ══ */
function renderCards(sorted) {
  document.getElementById('resultCount').textContent=`${sorted.length} de ${DATA.length}`;
  document.getElementById('gridSub').textContent=`${sorted.length} ativo${sorted.length!==1?'s':''}`;
  const grid=document.getElementById('cardGrid');
  if(sorted.length===0){grid.innerHTML=`<div class="empty-state"><strong>Nenhum ativo encontrado</strong>Não há registros para os filtros selecionados.<br><button class="reset-link" id="emptyReset">Limpar filtros</button></div>`;document.getElementById('emptyReset')?.addEventListener('click',clearFilters);return;}
  grid.innerHTML=sorted.map(d=>{
    const cc=clientColor(d.cliente);const sc=statusColor(d.status);const isOpen=d.status.toLowerCase().includes('aberto');
    const ageDays=getFaultAgeDays(d);const slaClass=getSlaClass(ageDays);const ageText=agePillText(ageDays);
    let cardSlaClass='';
    const isDeslig = isDesligamento(d);
    if(isSlaBreached(d))cardSlaClass='sla-breach';
    else if(isSlaWarning(d)||ageDays!==null&&ageDays>=10)cardSlaClass='sla-warning';
    const cardDesligClass = isDeslig ? 'card-desligamento' : '';
    const ageIcon=slaClass==='age-breach'?`<svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`:`<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`;
    const staleDays=getStaleDays(d);const isStale=staleDays!==null&&staleDays>=STALE_DAYS;
    const staleClass=isStale&&!cardSlaClass?'card-stale':'';
    const stalePillHtml=isStale?`<span class="stale-pill"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>Parado há ${staleDays} dia${staleDays!==1?'s':''}</span>`:'';
    return `<div class="card ${cardSlaClass} ${staleClass} ${cardDesligClass}" tabindex="0" role="button" aria-label="${esc(d.equipamento)} — ${esc(d.usina)}" style="--card-color:${cc.color};--card-glow:${cc.glow}" data-id="${esc(d.id)}">
      <div class="card-top"><div class="tag-row"><div class="icon-badge">${iconFor(d.equipamento)}</div><div><div class="usina-name">${esc(d.usina)}</div><div style="margin-top:3px;"><span style="font-size:10px;font-family:'Geist Mono',monospace;font-weight:700;letter-spacing:.04em;padding:2px 7px;border-radius:4px;background:${cc.color}22;color:${cc.color};border:1px solid ${cc.color}44;">${esc(d.cliente)}</span></div></div></div><div style="display:flex;flex-direction:column;align-items:flex-end;gap:5px;"><div class="status-led ${isOpen?'pulse':''}" style="--card-color:${sc};--card-glow:${statusGlow(d.status)}"></div><span class="sla-badge-card">SLA</span></div></div>
      <p class="equip">${esc(d.equipamento)}</p><p class="falha">${esc(d.falha)}</p>
      <div class="age-row"><span class="age-pill ${slaClass}">${ageIcon}${esc(ageText)}</span>${stalePillHtml}${isDeslig ? `<span class="deslig-badge"><svg viewBox="0 0 24 24"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>DESLIGADA</span>` : ''}</div>
      <div class="card-foot"><span class="status-chip" style="background:${statusBg(d.status)};border-color:${statusBorder(d.status)};color:${sc}">${esc(d.status)}</span><span class="see-more">histórico <svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg></span></div>
    </div>`;
  }).join('');
  grid.querySelectorAll('.card').forEach(c=>{c.addEventListener('click',()=>openModal(c.dataset.id));c.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openModal(c.dataset.id);}});});
}

/* ══ DRAWER ══ */
let _drawerList=[]; let _drawerIndex=-1;
function openModal(id) { _drawerList=_lastSorted.length?_lastSorted:filtered; _drawerIndex=_drawerList.findIndex(x=>x.id===id); if(_drawerIndex<0){const d=DATA.find(x=>x.id===id);if(d){_drawerList=[d];_drawerIndex=0;}else return;} _renderDrawer();_openDrawerPanel(); }
function _openDrawerPanel() { document.getElementById('drawer').classList.add('open');document.getElementById('drawerBackdrop').classList.add('open');document.body.classList.add('drawer-open');document.getElementById('drawerBody').scrollTop=0; }
function closeModal() { document.getElementById('drawer').classList.remove('open');document.getElementById('drawerBackdrop').classList.remove('open');document.body.classList.remove('drawer-open');document.querySelectorAll('.drawer-active').forEach(el=>el.classList.remove('drawer-active')); }
function _navigateDrawer(delta) { const next=_drawerIndex+delta; if(next<0||next>=_drawerList.length)return; _drawerIndex=next;_renderDrawer();document.getElementById('drawerBody').scrollTop=0; }
const ALL_STATUSES=['Em Aberto','Em Andamento','Aguardando Cliente','Aguardando Fabricante','Aguardando Equipamento','Corrigir Ronda - COS','Concluído','Resolvido','Fechado'];

function _renderDrawer() {
  const d=_drawerList[_drawerIndex]; if(!d)return;
  document.querySelectorAll('.drawer-active').forEach(el=>el.classList.remove('drawer-active'));
  const cardEl=document.querySelector(`.card[data-id="${CSS.escape(d.id)}"]`);
  const rowEl=document.querySelector(`tr[data-id="${CSS.escape(d.id)}"]`);
  if(cardEl){cardEl.classList.add('drawer-active');cardEl.scrollIntoView({behavior:'smooth',block:'nearest'});}
  if(rowEl){rowEl.classList.add('drawer-active');rowEl.scrollIntoView({behavior:'smooth',block:'nearest'});}
  const color=statusColor(d.status);const cc=clientColor(d.cliente);
  const ageDays=getFaultAgeDays(d);const slaClass=getSlaClass(ageDays);const opened=getOpenDate(d);
  const ageColor=slaClass==='age-breach'?'var(--red)':slaClass==='age-warning'?'var(--sla-warn)':'var(--teal)';
  const editing=canEdit();
  document.getElementById('drawerAccent').style.background=color;
  document.getElementById('drawer').style.setProperty('--drawer-color',color);
  document.getElementById('drawerEyebrow').innerHTML=`<span style="color:${cc.color}">${esc(d.cliente)}</span>`;
  document.getElementById('drawerTitle').textContent=d.equipamento;
  document.getElementById('drawerMeta').innerHTML=`${esc(d.usina)} &nbsp;·&nbsp; <span style="color:${color}">${esc(d.status)}</span>`;
  const total=_drawerList.length;
  document.getElementById('drawerCounter').textContent=`${_drawerIndex+1} / ${total}`;
  document.getElementById('drawerPrev').disabled=_drawerIndex===0;
  document.getElementById('drawerNext').disabled=_drawerIndex===total-1;
  document.getElementById('drawerActionBar').style.display=editing?'flex':'none';

  let ageHtml='';
  if(ageDays!==null&&ageDays>=0){
    const responseDate=getResponseDate(d);const responseDays=getResponseDays(d);
    const staleDaysD=getStaleDays(d);const lastActivity=getLastActivityDate(d);
    const openedLabel=opened?`Aberta em ${opened.toLocaleDateString('pt-BR')}`:'Data estimada';
    let slaLabel;
    if(isSlaBreached(d))slaLabel=`⚠ SLA VENCIDO — sem resposta da equipe há ${ageDays} dias`;
    else if(isSlaWarning(d))slaLabel=`· atenção — ${ageDays} dias sem resposta (prazo: ${SLA_CONFIG.BREACH_DAYS}d)`;
    else if(responseDate)slaLabel=`· equipe respondeu em ${responseDays} dia${responseDays!==1?'s':''} (${responseDate.toLocaleDateString('pt-BR')})`;
    else slaLabel=ageDays>=10?'· aguardando primeira resposta da equipe':'· recente';
    const staleHtml=(staleDaysD!==null&&staleDaysD>=STALE_DAYS)?`<div style="display:flex;align-items:center;gap:10px;background:rgba(83,96,112,.08);border:1px solid rgba(83,96,112,.25);border-radius:8px;padding:10px 13px;margin-top:10px;"><svg viewBox="0 0 24 24" style="width:18px;height:18px;stroke:#8FA0AE;fill:none;stroke-width:2;flex:none;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><div><div style="font-family:'Geist Mono',monospace;font-size:13px;font-weight:700;color:#8FA0AE;">Sem movimentação há ${staleDaysD} dia${staleDaysD!==1?'s':''}</div><div style="font-family:'Geist Mono',monospace;font-size:11px;color:var(--text-faint);margin-top:2px;">Última entrada: ${lastActivity?lastActivity.toLocaleDateString('pt-BR'):'desconhecida'}</div></div></div>`:'';
    ageHtml=`<div class="drawer-age-block" style="border-color:${ageColor}33;background:${ageColor}0D;"><svg viewBox="0 0 24 24" style="stroke:${ageColor}"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg><div style="flex:1;"><div class="age-num" style="color:${ageColor}">${ageDays} dia${ageDays!==1?'s':''} em aberto</div><div class="age-sub">${esc(openedLabel)} ${esc(slaLabel)}</div>${staleHtml}</div></div>`;
  }

  const entries=parseHistorico(d.historico);
  const tlHtml=entries.map(en=>`<div class="d-tl-item"><div class="d-tl-dot"></div>${en.date?`<div class="d-tl-date">${esc(en.date)}</div>`:''}<div class="d-tl-text">${esc(en.text)}</div></div>`).join('')||`<div class="d-tl-text" style="color:var(--text-faint)">Sem histórico.</div>`;

  if(editing){
    // Garante que o status atual aparece na lista mesmo se não estiver em ALL_STATUSES
const statusListCompleta = ALL_STATUSES.includes(d.status) ? ALL_STATUSES : [...ALL_STATUSES.slice(0,-3), d.status, ...ALL_STATUSES.slice(-3)];
const statusOpts=statusListCompleta.map(s=>`<option value="${esc(s)}" ${d.status===s?'selected':''}>${esc(s)}</option>`).join('');
    document.getElementById('drawerBody').innerHTML=`${ageHtml}<div class="edit-mode-banner"><svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>Modo edição — alterações são salvas diretamente na planilha</div><div class="d-edit-field"><div class="d-edit-label">Status</div><select class="d-edit-select" id="edit_status">${statusOpts}</select><div class="d-status-preview" id="statusPreview"></div></div><div class="d-edit-field"><div class="d-edit-label">Falha</div><textarea class="d-edit-textarea" id="edit_falha" rows="2">${esc(d.falha)}</textarea></div><div class="d-edit-field"><div class="d-edit-label">Causa</div><textarea class="d-edit-textarea" id="edit_causa" rows="2">${esc(d.causa)}</textarea></div><div class="d-edit-field"><div class="d-edit-label">Equipamentos impactados</div><input class="d-edit-input" id="edit_impactados" type="text" value="${esc(d.impactados)}"></div><div class="d-edit-field"><div class="d-edit-label">Ações Realizadas</div><textarea class="d-edit-textarea" id="edit_acao" rows="4">${esc(d.acao)}</textarea></div><div class="d-edit-field" style="display:flex;gap:10px;flex-wrap:wrap;"><div style="flex:1;min-width:130px;"><div class="d-edit-label">Ticket Fabricante</div><input class="d-edit-input" id="edit_ticket" type="text" value="${esc(d.ticketFabricante)}" placeholder="Ex: SOL-12345"></div><div style="flex:1;min-width:130px;"><div class="d-edit-label">Nº OS</div><input class="d-edit-input" id="edit_os" type="text" value="${esc(d.numeroOS)}" placeholder="Ex: 1596"></div></div><div class="d-edit-field"><div class="d-edit-label">Histórico cronológico</div><div class="d-timeline" style="margin-top:4px;margin-bottom:4px;">${tlHtml}</div></div>`;
    const statusSel=document.getElementById('edit_status');
    function updateStatusPreview(){const sc=statusColor(statusSel.value);document.getElementById('statusPreview').innerHTML=`<span class="d-status-dot" style="background:${sc}"></span><span style="font-size:11px;font-family:'Geist Mono',monospace;color:${sc};font-weight:600;">${esc(statusSel.value)}</span>`;}
    statusSel.addEventListener('change',updateStatusPreview);updateStatusPreview();
  } else {
    const chipsHtml=d.impactados?d.impactados.split(',').map(s=>s.trim()).filter(Boolean).map(s=>`<span class="chip">${esc(s)}</span>`).join(''):`<span style="color:var(--text-faint);font-size:12px;">Não informado</span>`;
    document.getElementById('drawerBody').innerHTML=`${ageHtml}<div class="d-section"><div class="d-label">Falha</div><div class="d-value">${esc(d.falha)}</div></div><div class="d-section"><div class="d-label">Causa</div><div class="d-value">${esc(d.causa)}</div></div><div class="d-section"><div class="d-label">Equipamentos impactados</div><div class="chips" style="margin-top:2px;">${chipsHtml}</div></div><div class="d-section"><div class="d-label">Ações Realizadas</div><div class="d-action-box">${esc(d.acao).replace(/\r\n|\r|\n/g,'<br>')}</div></div><div class="d-section"><div class="d-label">Ticket &amp; OS</div><div class="d-ticket-row"><div style="flex:1;min-width:130px;"><div class="d-ticket-label">Ticket Fabricante</div><div class="d-ticket-box ${d.ticketFabricante?'t-blue':'t-empty'}"><svg viewBox="0 0 24 24"><path d="M20 12V22H4V12"/><path d="M22 7H2v5h20V7z"/><path d="M12 22V7"/></svg>${d.ticketFabricante||'Não informado'}</div></div><div style="flex:1;min-width:130px;"><div class="d-ticket-label">Nº OS</div><div class="d-ticket-box ${d.numeroOS?'t-teal':'t-empty'}"><svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>${d.numeroOS||'Não informado'}</div></div></div></div><div class="d-section"><div class="d-label">Histórico cronológico</div><div class="d-timeline" style="margin-top:8px;">${tlHtml}</div></div>`;
  }
}

async function _saveDrawerToSheet() {
  const d=_drawerList[_drawerIndex]; if(!d||!canEdit())return;
  if(!APPS_SCRIPT_URL){showToast('Cole a URL do Apps Script em APPS_SCRIPT_URL no código.',false);return;}
  const btn=document.getElementById('drawerSaveBtn');btn.disabled=true;btn.innerHTML=`<span class="save-spinner"></span> Salvando…`;
  const fields={status:document.getElementById('edit_status')?.value,falha:document.getElementById('edit_falha')?.value,causa:document.getElementById('edit_causa')?.value,impactados:document.getElementById('edit_impactados')?.value,acao:document.getElementById('edit_acao')?.value,ticketFabricante:document.getElementById('edit_ticket')?.value,numeroOS:document.getElementById('edit_os')?.value};
  const editor=_currentSession.user||_currentSession.role||'dashboard';
  const changed=[];
  for(const[field,value]of Object.entries(fields)){if(value===undefined)continue;const original=d[field]||'';if(value.trim()===original.trim())continue;changed.push({field,value:value.trim()});}
  if(changed.length===0){showToast('Nenhuma alteração detectada.',true);btn.disabled=false;btn.innerHTML=`<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg> Salvar na planilha`;return;}
  const errors=[];
  for(const{field,value}of changed){try{await fetch(APPS_SCRIPT_URL,{method:'POST',mode:'no-cors',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:d.id,field,value,editor})});d[field]=value;}catch(err){errors.push(field);}}
  const newColor=statusColor(d.status);
  document.getElementById('drawerAccent').style.background=newColor;
  document.getElementById('drawerMeta').innerHTML=`${esc(d.usina)} &nbsp;·&nbsp; <span style="color:${newColor}">${esc(d.status)}</span>`;
  btn.disabled=false;btn.innerHTML=`<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg> Salvar na planilha`;
  if(errors.length===0){showToast(`${changed.length} campo${changed.length>1?'s':''} salvo${changed.length>1?'s':''}!`,true);applySortAndRender();}
  else showToast(`Erro ao salvar: ${errors.join(', ')}`,false);
}

async function _addHistoryEntry() {
  const d=_drawerList[_drawerIndex]; if(!d||!canEdit())return;
  if(!APPS_SCRIPT_URL){showToast('Cole a URL do Apps Script em APPS_SCRIPT_URL no código.',false);return;}
  const entry=prompt('Nova entrada no histórico:');if(!entry||!entry.trim())return;
  const editor=_currentSession.user||_currentSession.role||'dashboard';
  try{await fetch(APPS_SCRIPT_URL,{method:'POST',mode:'no-cors',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:d.id,field:'historico',value:entry.trim(),append:true,editor})});
  const now=new Date();const dateStr=now.toLocaleDateString('pt-BR')+' '+now.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  d.historico=d.historico?`${d.historico}\n${dateStr} - ${entry.trim()}`:`${dateStr} - ${entry.trim()}`;
  showToast('Entrada adicionada ao histórico!',true);_renderDrawer();}
  catch(err){showToast('Erro ao adicionar ao histórico.',false);}
}

/* ══ VERIFICAR RONDAS ══
   ⚠ URL CORRIGIDA: whatsapp-painel-falhas.onrender.com
   (o nome real do serviço Python no Render)
══ */
const RONDAS_URL = 'https://whatsapp-painel-falhas.onrender.com';

async function verificarRondas() {
  const btn=document.getElementById('rondasBtn');
  btn.disabled=true;btn.classList.add('loading');
  btn.innerHTML=`<svg viewBox="0 0 24 24"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg> Buscando…`;
  try {
    const res=await fetch(`${RONDAS_URL}/rondas`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({horas:6})});
    if(!res.ok)throw new Error(`Servidor retornou ${res.status}`);
    const data=await res.json();
    if(!data.ok)throw new Error(data.error||'Erro desconhecido');
    const novos=(data.novos||[]);const atualizados=(data.atualizados||[]);const normalizados=(data.normalizados||[]);const ignorados=data.ignorados||0;
    const totalAcoes=novos.length+atualizados.length+normalizados.length;

    // Monta chips clicáveis para cada grupo de ocorrências
    function makeChips(lista, cor) {
      if(!lista||lista.length===0) return '';
      return `<div class="rondas-item-list open">`+
        lista.map(item=>{
          const usina=typeof item==='object'?item.usina:item;
          const id=typeof item==='object'?item.id:'';
          return `<span class="rondas-item-chip" data-usina="${esc(usina||'')}" data-id="${esc(String(id||''))}" style="border-color:${cor}33;color:${cor};">
            <svg viewBox="0 0 24 24" style="width:10px;height:10px;stroke:currentColor;fill:none;stroke-width:2.5;"><polyline points="9 18 15 12 9 6"/></svg>
            ${esc(usina||'')}${id?' · #'+id:''}
          </span>`;
        }).join('')+`</div>`;
    }

    // Reseta para modo resultado (esconde 2 colunas se estava aberto)
    document.getElementById('rondasBodyGrupos').style.display = 'none';
    document.getElementById('rondasResult').style.display = '';
    document.getElementById('rondasModalTitulo').textContent = 'Verificar Rondas';

    document.getElementById('rondasResult').innerHTML=`
      <div class="rondas-result-row clickable" id="rr-novos">
        <span style="color:var(--text-dim)">Novas ocorrências criadas</span>
        <span class="rondas-result-num" style="color:var(--amber)">${novos.length}</span>
      </div>
      ${makeChips(novos,'var(--amber)')}
      <div class="rondas-result-row clickable" id="rr-atualizados">
        <span style="color:var(--text-dim)">Ocorrências atualizadas</span>
        <span class="rondas-result-num" style="color:var(--teal)">${atualizados.length}</span>
      </div>
      ${makeChips(atualizados,'var(--teal)')}
      <div class="rondas-result-row clickable" id="rr-normalizados">
        <span style="color:var(--text-dim)">Normalizadas / Concluídas</span>
        <span class="rondas-result-num" style="color:var(--sla-ok)">${normalizados.length}</span>
      </div>
      ${makeChips(normalizados,'var(--sla-ok)')}
      <div class="rondas-result-row">
        <span style="color:var(--text-faint)">Sem dados relevantes</span>
        <span class="rondas-result-num" style="color:var(--text-faint)">${ignorados}</span>
      </div>
      ${totalAcoes>0
        ?`<div style="margin-top:12px;padding:10px 12px;background:rgba(63,193,176,.08);border:1px solid rgba(63,193,176,.25);border-radius:8px;font-family:'Geist Mono',monospace;font-size:11px;color:var(--teal);">Planilha atualizada — recarregando dados em 3s…</div>`
        :`<div style="margin-top:12px;padding:10px 12px;background:var(--panel-alt);border:1px solid var(--border);border-radius:8px;font-family:'Geist Mono',monospace;font-size:11px;color:var(--text-faint);">Nenhuma ronda nova encontrada nas últimas 6h</div>`
      }
      <div style="margin-top:8px;font-family:'Geist Mono',monospace;font-size:10px;color:var(--text-faint);">Informações duplicadas são ignoradas automaticamente.</div>
    `;

    // Chips clicáveis — abre drawer do card correspondente
    document.querySelectorAll('.rondas-item-chip').forEach(chip=>{
      chip.addEventListener('click', ()=>{
        const id=chip.dataset.id;
        const usina=chip.dataset.usina;
        document.getElementById('rondasOverlay').classList.remove('open');
        // Tenta abrir pelo ID primeiro, depois pela usina
        if(id){
          setTimeout(()=>openModal(id), 200);
        } else if(usina){
          const d=DATA.find(x=>x.usina===usina);
          if(d) setTimeout(()=>openModal(d.id), 200);
        }
      });
    });

    // Botão buscar rondas por grupo
    const btnGrupos=document.getElementById('btnBuscarGrupos');
    if(btnGrupos) btnGrupos.addEventListener('click', buscarRondasPorGrupo);

    document.getElementById('rondasOverlay').classList.add('open');
    if(totalAcoes>0) setTimeout(()=>fetchSheet(),3000);
  } catch(err) {
    showToast(`Erro ao verificar rondas: ${err.message}`,false);console.error('[Rondas]',err);
  } finally {
    btn.disabled=false;btn.classList.remove('loading');
    btn.innerHTML=`<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> Verificar Rondas`;
  }
}

/* ══ BUSCAR RONDAS POR GRUPO ══ */
const GRUPOS_NOMES = {
  '120363423233716775@g.us': '[O&M] Renogrid',
  '120363423427343356@g.us': '[O&M] Thopen',
  '120363402559504115@g.us': '[O&M] 2C',
  '120363426381032089@g.us': '[O&M] Alves Lima',
  '120363423844956611@g.us': '[O&M] GD Energy',
  '120363421162420788@g.us': 'COS Centro-Oeste',
  '120363425837962709@g.us': 'COS Sul',
  '120363402176878100@g.us': 'COS Nordeste',
  '120363423533840348@g.us': 'COS Sudeste',
  '120363421052607450@g.us': 'COS Norte',
};

/* Estado dos grupos carregados */
let _rondasGruposData = [];
let _rondasGrupoAtivo = null;

async function buscarRondasPorGrupo() {
  const btn = document.getElementById('btnBuscarGrupos');
  if(btn){ btn.innerHTML=`<svg viewBox="0 0 24 24" style="width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;animation:spin .7s linear infinite"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg> Buscando…`; btn.style.opacity='.6'; btn.disabled=true; }

  try {
    const res = await fetch(`${RONDAS_URL}/rondas/grupos`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({horas: 24})
    });
    if(!res.ok) throw new Error(`Servidor retornou ${res.status}`);
    const data = await res.json();
    _rondasGruposData = data.grupos || [];

    // Mostra layout de 2 colunas
    document.getElementById('rondasResult').style.display = 'none';
    document.getElementById('rondasBodyGrupos').style.display = 'flex';
    document.getElementById('rondasModalTitulo').textContent = 'Rondas — Últimas 24h';

    // Monta sidebar
    renderRondasSidebar();

    // Seleciona o primeiro grupo com mensagens automaticamente
    const comMsgs = _rondasGruposData.find(g => g.total > 0);
    if(comMsgs) selecionarGrupoRonda(comMsgs.id);
    else selecionarGrupoRonda(_rondasGruposData[0]?.id);

  } catch(err) {
    showToast('Erro ao buscar grupos: ' + err.message, false);
  } finally {
    if(btn){
      btn.innerHTML=`<svg viewBox="0 0 24 24" style="width:12px;height:12px;stroke:currentColor;fill:none;stroke-width:2;"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> Últimas rondas por grupo`;
      btn.style.opacity='1'; btn.disabled=false;
    }
  }
}

function renderRondasSidebar() {
  const sidebar = document.getElementById('rondasSidebar');
  if(!sidebar) return;

  sidebar.innerHTML = _rondasGruposData.map(g => {
    const nome = GRUPOS_NOMES[g.id] || g.id;
    const temMsgs = (g.total||0) > 0;
    const temPendente = (g.mensagens||[]).some(m => !m.processado);
    const dotCls = temPendente ? 'has-pendentes' : temMsgs ? 'has-msgs' : '';
    return `<div class="rondas-grupo-item" data-gid="${esc(g.id)}" title="${esc(nome)}">
      <div class="rg-dot ${dotCls}"></div>
      <div class="rg-info">
        <div class="rg-nome">${esc(nome)}</div>
        <div class="rg-count">${g.total||0} msgs</div>
      </div>
    </div>`;
  }).join('');

  sidebar.querySelectorAll('.rondas-grupo-item').forEach(el => {
    el.addEventListener('click', () => selecionarGrupoRonda(el.dataset.gid));
  });
}

function selecionarGrupoRonda(gid) {
  if(!gid) return;
  _rondasGrupoAtivo = gid;

  // Atualiza sidebar
  document.querySelectorAll('.rondas-grupo-item').forEach(el => {
    el.classList.toggle('active', el.dataset.gid === gid);
  });

  const grupo = _rondasGruposData.find(g => g.id === gid);
  if(!grupo) return;

  const nome = GRUPOS_NOMES[gid] || gid;
  document.getElementById('rondasGrupoTitulo').textContent = nome;
  document.getElementById('rondasGrupoSub').textContent =
    `${grupo.total||0} mensagem(s) nas últimas 24h`;

  const lista = document.getElementById('rondasMsgsList');
  if(!lista) return;

  const msgs = grupo.mensagens || [];
  if(msgs.length === 0) {
    lista.innerHTML = `<div class="rondas-empty">
      <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
      <span>Nenhuma mensagem nas últimas 24h</span>
    </div>`;
    return;
  }

  // Renderiza cards de mensagem
  // O backend retorna do mais antigo para o mais novo → último índice = mais recente
  const ultimoIdx = msgs.length - 1;
  lista.innerHTML = msgs.map((m, mi) => {
    const isLatest = mi === ultimoIdx;
    const procBadge = m.processado
      ? `<span class="ronda-msg-badge proc">✅ processado</span>`
      : `<span class="ronda-msg-badge pend">⏳ pendente</span>`;
    const ts = m.timestamp
      ? `<span class="ronda-msg-ts">${esc(m.timestamp)}</span>`
      : '';
    const texto = esc(m.texto || '');
    // Card mais recente: aberto por padrão. Antigos: fechados por padrão
    const bodyOpen = isLatest ? 'open' : '';
    const iconOpen = isLatest ? 'open' : '';
    const latestClass = isLatest ? 'latest' : '';
    const latestLabel = isLatest
      ? `<span style="font-family:'Geist Mono',monospace;font-size:9px;color:var(--teal);font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin-left:4px;">↑ mais recente</span>`
      : '';
    return `<div class="ronda-msg-card ${latestClass}">
      <div class="ronda-msg-card-head" data-card="${gid}-${mi}">
        <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:0;">
          ${ts}${latestLabel}
        </div>
        ${procBadge}
        <button class="ronda-msg-toggle-icon ${iconOpen}" data-card="${gid}-${mi}" title="${isLatest?'Recolher':'Expandir'}">
          <svg viewBox="0 0 24 24"><polyline points="${isLatest?'18 15 12 9 6 15':'6 9 12 15 18 9'}"/></svg>
        </button>
      </div>
      <div class="ronda-msg-body ${bodyOpen}" id="rmb-${gid.replace(/[^a-z0-9]/gi,'-')}-${mi}">${texto}</div>
    </div>`;
  }).join('');

  // Toggle de cada card
  lista.querySelectorAll('.ronda-msg-card-head').forEach(head => {
    head.addEventListener('click', () => {
      const [gidPart, miPart] = head.dataset.card.split(/(?<=\D)(?=\d+$)/);
      // reconstrói o id do body: rmb-{gid_safe}-{mi}
      const card = head.closest('.ronda-msg-card');
      const body = card?.querySelector('.ronda-msg-body');
      const icon = head.querySelector('.ronda-msg-toggle-icon');
      if(!body) return;
      const isOpen = body.classList.toggle('open');
      icon?.classList.toggle('open', isOpen);
      if(icon) {
        icon.title = isOpen ? 'Recolher' : 'Expandir';
        icon.innerHTML = `<svg viewBox="0 0 24 24"><polyline points="${isOpen?'18 15 12 9 6 15':'6 9 12 15 18 9'}"/></svg>`;
      }
    });
  });

  // Scroll para o topo
  lista.scrollTop = 0;
}

try { document.getElementById('rondasBtn').addEventListener('click',verificarRondas); document.getElementById('rondasCloseBtn').addEventListener('click',()=>document.getElementById('rondasOverlay').classList.remove('open')); document.getElementById('rondasOverlay').addEventListener('click',e=>{if(e.target.id==='rondasOverlay')document.getElementById('rondasOverlay').classList.remove('open');}); } catch(e){}

try { document.getElementById('drawerClose').addEventListener('click',closeModal); } catch(e){}
document.getElementById('drawerBackdrop').addEventListener('click',closeModal);
document.getElementById('drawerPrev').addEventListener('click',()=>_navigateDrawer(-1));
document.getElementById('drawerNext').addEventListener('click',()=>_navigateDrawer(1));
document.getElementById('drawerSaveBtn').addEventListener('click',_saveDrawerToSheet);
document.getElementById('drawerHistBtn').addEventListener('click',_addHistoryEntry);
document.addEventListener('keydown',e=>{const drawerOpen=document.getElementById('drawer').classList.contains('open');if(e.key==='Escape'){closeModal();closeChamados();}if(!drawerOpen)return;if(e.key==='ArrowLeft'||e.key==='ArrowUp'){e.preventDefault();_navigateDrawer(-1);}if(e.key==='ArrowRight'||e.key==='ArrowDown'){e.preventDefault();_navigateDrawer(1);}});

/* ══ VIEW MODE ══ */
let viewMode='cards';let tablePage=1;let tablePageSize=20;let tableSortKey='id';let tableSortDir=1;let _lastSorted=[];
document.getElementById('viewCards').addEventListener('click',()=>setViewMode('cards'));
document.getElementById('viewTable').addEventListener('click',()=>setViewMode('table'));
function setViewMode(mode){viewMode=mode;document.getElementById('viewCards').classList.toggle('active',mode==='cards');document.getElementById('viewTable').classList.toggle('active',mode==='table');document.getElementById('sortGroup').style.display=mode==='cards'?'flex':'none';if(mode==='cards'){document.getElementById('cardGrid').style.display='';document.getElementById('tableWrap').style.display='none';renderCards(_lastSorted);}else{document.getElementById('cardGrid').style.display='none';document.getElementById('tableWrap').style.display='';tablePage=1;renderTable(_lastSorted);}}

/* ══ TABELA ══ */
const TABLE_COLS=[{key:'id',label:'ID',sortable:true,width:'56px'},{key:'usina',label:'Cliente / Usina',sortable:true,width:'155px'},{key:'equip',label:'Equipamento',sortable:true,width:'165px'},{key:'falha',label:'Falha',sortable:false,width:''},{key:'status',label:'Status',sortable:true,width:'140px'},{key:'age',label:'Idade',sortable:true,width:'130px'},{key:'ticket',label:'Ticket',sortable:false,width:'110px'},{key:'det',label:'',sortable:false,width:'72px'}];
function sortTableData(data){return[...data].sort(function(a,b){var va,vb;if(tableSortKey==='id'){va=+a.id;vb=+b.id;}else if(tableSortKey==='newest'){return +b.id-+a.id;}else if(tableSortKey==='usina'){va=a.usina;vb=b.usina;}else if(tableSortKey==='equip'){va=a.equipamento;vb=b.equipamento;}else if(tableSortKey==='status'){va=a.status;vb=b.status;}else if(tableSortKey==='age'){va=getFaultAgeDays(a);if(va===null)va=-999;vb=getFaultAgeDays(b);if(vb===null)vb=-999;}else return 0;if(typeof va==='string')return va.localeCompare(vb)*tableSortDir;return(va-vb)*tableSortDir;});}
function renderTable(data){
  var wrap=document.getElementById('tableWrap');
  if(!data||data.length===0){wrap.innerHTML='<div class="empty-state" style="grid-column:unset"><strong>Nenhum ativo encontrado</strong>Não há registros para os filtros selecionados.<br><button class="reset-link" id="emptyReset2">Limpar filtros</button></div>';var er=document.getElementById('emptyReset2');if(er)er.addEventListener('click',clearFilters);return;}
  var sorted=sortTableData(data);var total=sorted.length;var pages=Math.ceil(total/tablePageSize);if(tablePage>pages)tablePage=pages;var start=(tablePage-1)*tablePageSize;var pageData=sorted.slice(start,start+tablePageSize);
  var thCells=TABLE_COLS.map(function(col){var isSorted=col.sortable&&tableSortKey===col.key;var arrowUp='<svg viewBox="0 0 24 24"><polyline points="18 15 12 9 6 15"/></svg>';var arrowDown='<svg viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9"/></svg>';var arrow=tableSortDir===1?arrowUp:arrowDown;var sortIcon=col.sortable?'<span class="th-sort-icon">'+(isSorted?arrow:arrowUp)+'</span>':'';var cls=(col.sortable?'sortable':'')+(isSorted?' th-sorted':'');var wstyle=col.width?' style="width:'+col.width+';min-width:'+col.width+'"':'';var dcol=col.sortable?' data-col="'+col.key+'"':'';return'<th class="'+cls+'"'+wstyle+dcol+'>'+col.label+sortIcon+'</th>';}).join('');
  var rows=pageData.map(function(d){var cc=clientColor(d.cliente);var sc=statusColor(d.status);var ageDays=getFaultAgeDays(d);var slaClass=getSlaClass(ageDays);var ageText=agePillText(ageDays);var trClass=isSlaBreached(d)?'tr-breach':(isSlaWarning(d)||(ageDays!==null&&ageDays>=10))?'tr-warning':'';var ageIcon=(slaClass==='age-breach')?'<svg viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>':'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';var ticketHTML=isValidTicket(d.ticketFabricante)?esc(d.ticketFabricante):'—';var ticketCls=isValidTicket(d.ticketFabricante)?'td-ticket':'td-ticket empty';var staleDays2=getStaleDays(d);var stalePillTd=(staleDays2!==null&&staleDays2>=STALE_DAYS)?'<span class="td-stale-pill" style="margin-left:5px;"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'+staleDays2+'d parado</span>':'';
  return'<tr class="'+trClass+'" data-id="'+esc(d.id)+'" tabindex="0">'+'<td class="td-id">#'+d.id.padStart(2,'0')+'</td>'+'<td class="td-usina"><span class="td-client-tag" style="background:'+cc.color+'22;color:'+cc.color+';border:1px solid '+cc.color+'44;">'+esc(d.cliente)+'</span><div class="td-usina-name">'+esc(d.usina)+'</div></td>'+'<td class="td-equip">'+esc(d.equipamento)+'</td>'+'<td class="td-falha"><div class="td-falha-text" title="'+esc(d.falha)+'">'+esc(d.falha)+'</div></td>'+'<td class="td-status"><span class="td-status-chip" style="background:'+statusBg(d.status)+';border-color:'+statusBorder(d.status)+';color:'+sc+'">'+esc(d.status)+'</span></td>'+'<td class="td-age"><span class="td-age-pill '+slaClass+'">'+ageIcon+esc(ageText)+'</span>'+stalePillTd+'</td>'+'<td class="'+ticketCls+'">'+ticketHTML+'</td>'+'<td class="td-action"><button class="td-see-btn" data-id="'+esc(d.id)+'">Ver <svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg></button></td>'+'</tr>';}).join('');
  var pageBtns='';if(pages>1){var maxBtns=5;var sp=Math.max(1,tablePage-Math.floor(maxBtns/2));var ep=Math.min(pages,sp+maxBtns-1);if(ep-sp<maxBtns-1)sp=Math.max(1,ep-maxBtns+1);if(sp>1)pageBtns+='<button class="page-btn" data-p="1">1</button>'+(sp>2?'<span style="color:var(--text-faint);padding:0 4px">…</span>':'');for(var p=sp;p<=ep;p++)pageBtns+='<button class="page-btn'+(p===tablePage?' active':'')+'" data-p="'+p+'">'+p+'</button>';if(ep<pages)pageBtns+=(ep<pages-1?'<span style="color:var(--text-faint);padding:0 4px">…</span>':'')+'<button class="page-btn" data-p="'+pages+'">'+pages+'</button>';}
  var rowOpts=[10,20,50,100].map(function(n){return'<option value="'+n+'"'+(tablePageSize===n?' selected':'')+'>'+n+'</option>';}).join('');
  wrap.innerHTML='<table class="fault-table"><thead><tr>'+thCells+'</tr></thead><tbody>'+rows+'</tbody></table><div class="table-pagination"><div class="page-info">Exibindo '+(start+1)+'–'+Math.min(start+tablePageSize,total)+' de '+total+' registros</div><div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;"><div style="display:flex;align-items:center;gap:6px;"><span style="font-size:11px;color:var(--text-faint);font-family:\'IBM Plex Mono\',monospace;">linhas:</span><select class="rows-select" id="rowsSelect">'+rowOpts+'</select></div><div class="page-btns"><button class="page-btn" data-p="'+(tablePage-1)+'"'+(tablePage===1?' disabled':'')+'>‹</button>'+pageBtns+'<button class="page-btn" data-p="'+(tablePage+1)+'"'+(tablePage===pages?' disabled':'')+'>›</button></div></div></div>';
  wrap.querySelectorAll('th.sortable').forEach(function(th){th.addEventListener('click',function(){var col=th.dataset.col;if(tableSortKey===col)tableSortDir*=-1;else{tableSortKey=col;tableSortDir=1;}renderTable(data);});});
  wrap.querySelectorAll('tbody tr').forEach(function(tr){tr.addEventListener('click',function(){openModal(tr.dataset.id);});tr.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();openModal(tr.dataset.id);}});});
  wrap.querySelectorAll('.td-see-btn').forEach(function(btn){btn.addEventListener('click',function(e){e.stopPropagation();openModal(btn.dataset.id);});});
  wrap.querySelectorAll('.page-btn:not([disabled])').forEach(function(btn){btn.addEventListener('click',function(){tablePage=parseInt(btn.dataset.p);renderTable(data);});});
  var rowsSel=wrap.querySelector('#rowsSelect');if(rowsSel)rowsSel.addEventListener('change',function(){tablePageSize=parseInt(rowsSel.value);tablePage=1;renderTable(data);});
}

/* ══ SORT BUTTONS ══ */
document.querySelectorAll('.sort-btn').forEach(btn=>{btn.addEventListener('click',()=>{sortKey=btn.dataset.sort;document.querySelectorAll('.sort-btn').forEach(b=>b.classList.remove('active'));btn.classList.add('active');applySortAndRender();});});

/* ══ LISTENERS ══ */
// Abas Ativas / Histórico
document.getElementById('tabAtivas').addEventListener('click', () => {
  activeTab = 'ativas';
  document.getElementById('tabAtivas').classList.add('active');
  document.getElementById('tabHistorico').classList.remove('active');
  // Reseta filtros de SLA e desligamento ao trocar aba
  activeSlaFilter = false; activeDesligFilter = false;
  document.getElementById('slaBanner').classList.remove('active-filter');
  document.getElementById('desligBanner').classList.remove('active-filter');
  applyFilters();
});
document.getElementById('tabHistorico').addEventListener('click', () => {
  activeTab = 'historico';
  document.getElementById('tabHistorico').classList.add('active');
  document.getElementById('tabAtivas').classList.remove('active');
  activeSlaFilter = false; activeDesligFilter = false;
  document.getElementById('slaBanner').classList.remove('active-filter');
  document.getElementById('desligBanner').classList.remove('active-filter');
  applyFilters();
});

document.getElementById('fSearch').addEventListener('input',applyFilters);
document.getElementById('fDateFrom').addEventListener('change',applyFilters);
document.getElementById('fDateTo').addEventListener('change',applyFilters);
document.getElementById('clearBtn').addEventListener('click',clearFilters);
document.getElementById('updateBtn').addEventListener('click',fetchSheet);
document.getElementById('slaBanner').addEventListener('click',()=>{activeSlaFilter=!activeSlaFilter;document.getElementById('slaBanner').classList.toggle('active-filter',activeSlaFilter);if(activeSlaFilter)document.getElementById('cardGrid').scrollIntoView({behavior:'smooth',block:'start'});applyFilters();});
document.getElementById('desligBanner').addEventListener('click', toggleDesligFilter);

/* ══ CHAMADOS ══ */
function openChamados(){
  const withTicket=filtered.filter(d=>isValidTicket(d.ticketFabricante));
  const body=document.getElementById('chBody');const sub=document.getElementById('chSubtitle');
  const totalAbertos=withTicket.filter(d=>{const s=d.status.toLowerCase();return!s.includes('conclu')&&!s.includes('resolv')&&!s.includes('fechad');}).length;
  sub.textContent=`${withTicket.length} chamado(s) com ticket · ${totalAbertos} em aberto`;
  if(withTicket.length===0){body.innerHTML=`<div class="ch-empty">Nenhum registro com ticket fabricante encontrado.</div>`;}
  else{body.innerHTML=withTicket.map(d=>{const sc=statusColor(d.status);const cc=clientColor(d.cliente);const ageDays=getFaultAgeDays(d);const ageTxt=ageDays!==null&&ageDays>=0?agePillText(ageDays):'';return`<div class="ch-item" data-id="${esc(d.id)}" style="cursor:pointer;"><div class="ch-ticket">${esc(d.ticketFabricante)}</div><div class="ch-info"><div class="ch-equip">${esc(d.equipamento)}</div><div class="ch-meta"><span style="color:${cc.color}">${esc(d.usina)}</span>${d.numeroOS?` · OS: ${esc(d.numeroOS)}`:''} ${ageTxt?` · <span style="color:${getSlaClass(ageDays)==='age-breach'?'var(--red)':'var(--text-faint)'}">${esc(ageTxt)}</span>`:''}</div></div><div class="ch-status"><span style="font-size:11px;font-family:'Geist Mono',monospace;font-weight:700;padding:3px 10px;border-radius:6px;background:${sc}1A;color:${sc};border:1px solid ${sc}44;">${esc(d.status)}</span></div></div>`;}).join('');body.querySelectorAll('.ch-item').forEach(el=>{el.addEventListener('click',()=>{closeChamados();openModal(el.dataset.id);});});}
  document.getElementById('chOverlay').classList.add('open');
}
function closeChamados(){document.getElementById('chOverlay').classList.remove('open');}
document.getElementById('chamadosBtn').addEventListener('click',openChamados);
document.getElementById('chClose').addEventListener('click',closeChamados);
document.getElementById('chOverlay').addEventListener('click',e=>{if(e.target.id==='chOverlay')closeChamados();});

/* ══ MODO CLIENTE ══ */
const URL_CLIENTE=(()=>{const p=new URLSearchParams(window.location.search).get('cliente');return p?p.trim():null;})();
function activateClientMode(clienteParam){
  const match=DATA.find(d=>d.cliente.toUpperCase()===clienteParam.toUpperCase());
  const clienteName=match?match.cliente:clienteParam;const cc=clientColor(clienteName);
  DATA=DATA.filter(d=>d.cliente.toUpperCase()===clienteParam.toUpperCase());
  document.body.classList.add('client-mode');
  const banner=document.getElementById('clientModeBanner');banner.classList.add('visible');banner.style.color=cc.color;banner.style.background=cc.color+'12';banner.style.borderColor=cc.color+'35';
  document.getElementById('clientModeLabel').textContent=clienteName;
  document.querySelector('.sub').textContent=`Painel de operação e manutenção — ${clienteName}`;
  document.getElementById('clientLegend').style.display='none';document.getElementById('ms-cliente').style.display='none';document.getElementById('updateBtn').style.display='none';document.getElementById('sourceBadge').style.display='none';document.getElementById('totalBadge').style.display='none';document.getElementById('clearBtn').style.display='none';document.getElementById('countdownWrap').style.display='none';
  document.querySelector('footer').innerHTML=`Painel de falhas · ${clienteName}<br>Clique em um card para abrir o histórico completo de ações`;
}
function gerarLinks(){const base=window.location.href.split('?')[0];const clientes=[...new Set(DATA.map(d=>d.cliente))].sort();console.group('🔗 Links por cliente');clientes.forEach(c=>console.log(`${c}:\n${base}?cliente=${encodeURIComponent(c)}`));console.groupEnd();return clientes.map(c=>({cliente:c,url:`${base}?cliente=${encodeURIComponent(c)}`}));}
window.gerarLinks=gerarLinks;

/* ══ ZELADORIAS ══ */
const ZEL_TIPOS={limpeza:{label:'Lavagem dos Módulos',color:'var(--blue)',key:'limpeza'},supressao:{label:'Roçada',color:'var(--sla-ok)',key:'supressao'},poda:{label:'Poda Química',color:'var(--amber)',key:'poda'}};
const ZEL_STATUS=['Agendado','Em Andamento','Concluído','Atrasado','Pendente'];
const ZEL_STATUS_CLASS={'Agendado':'zs-agendado','Em Andamento':'zs-em-andamento','Concluído':'zs-concluido','Atrasado':'zs-atrasado','Pendente':'zs-pendente'};
let zelTab='supressao'; let zelData={limpeza:[],supressao:[],poda:[]}; let _zelTimer=null; let _zelFromSheet=false;
const ZEL_GID='987654321';

function zelNormalizeStatus(raw){const v=(raw||'').toLowerCase().trim();if(v.includes('andamento')||v.includes('execuc')||v.includes('progress'))return 'Em Andamento';if(v.includes('conclu')||v.includes('realiz')||v.includes('done')||v.includes('feito'))return 'Concluído';if(v.includes('atras')||v.includes('vencid')||v.includes('overdue'))return 'Atrasado';if(v.includes('agend')||v.includes('program')||v.includes('scheduled'))return 'Agendado';if(v===''||v.includes('pend')||v.includes('aguard'))return 'Pendente';return raw||'Pendente';}
function zelFormatDate(raw){if(!raw||raw==='—'||raw==='-')return '';if(/^\d{2}\/\d{2}\/\d{4}$/.test(raw))return raw;if(/^\d{1,2}\/\d{1,2}\/\d{4}$/.test(raw)){const[m,d,y]=raw.split('/');return`${d.padStart(2,'0')}/${m.padStart(2,'0')}/${y}`;}if(/^\d{4}-\d{2}-\d{2}/.test(raw)){const[y,m,d]=raw.substring(0,10).split('-');return`${d}/${m}/${y}`;}return raw;}
function zelEmptyFromData(){const usinas=[...new Map(DATA.map(d=>[d.usina,{cliente:d.cliente,usina:d.usina}])).values()];function makeEmpty(list){return list.map((u,i)=>({id:`${u.usina}_empty_${i}`,cliente:u.cliente,usina:u.usina,status:'Pendente',previsto:'',realizado:'',prox:'',qtd:'',obs:''}));}return{limpeza:makeEmpty(usinas),supressao:makeEmpty(usinas),poda:makeEmpty(usinas)};}
function zelMergeEdits(novo){try{const saved=localStorage.getItem('om_zeladorias_edits');if(!saved)return;const edits=JSON.parse(saved);Object.keys(novo).forEach(tipo=>{novo[tipo].forEach(row=>{const key=row.usina+'_'+tipo;if(edits[key]){if(edits[key].status!==undefined)row.status=edits[key].status;if(edits[key].obs!==undefined)row.obs=edits[key].obs;}});});}catch(e){}}
function zelSaveEdit(usina,tipo,field,value){try{const saved=localStorage.getItem('om_zeladorias_edits');const edits=saved?JSON.parse(saved):{};const key=usina+'_'+tipo;if(!edits[key])edits[key]={};edits[key][field]=value;localStorage.setItem('om_zeladorias_edits',JSON.stringify(edits));}catch(e){}}
function zelSave(showBadge){if(!_zelFromSheet)localStorage.setItem('om_zeladorias',JSON.stringify(zelData));if(showBadge!==false){const badge=document.getElementById('zelSaveBadge');if(badge){badge.classList.add('show');clearTimeout(_zelTimer);_zelTimer=setTimeout(()=>badge.classList.remove('show'),2200);}}}

async function zelFetchSheet(){
  const badge=document.getElementById('zelSourceBadge');const label=document.getElementById('zelSourceLabel');const btn=document.getElementById('zelRefreshBtn');
  if(badge)badge.className='source-badge loading';if(label)label.textContent='buscando…';if(btn){btn.disabled=true;btn.classList.add('spinning');}
  try{
    const csvUrl=`https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=csv&gid=${ZEL_GID}&t=${Date.now()}`;
    const res=await fetch(csvUrl);if(!res.ok)throw new Error(`HTTP ${res.status}`);
    const csv=await res.text();const allRows=splitCSVRows(csv);if(allRows.length<3)throw new Error('CSV vazio');
    const row1=parseCSVLine(allRows[0]).map(v=>v.trim());const row2=parseCSVLine(allRows[1]).map(v=>v.trim());
    function norm(s){return(s||'').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/\s+/g,' ').trim();}
    const GRUPO_MAP={supressao:['rocada','rocagem','supressao vegetal','supressao','vegetal','rosada','rocada'],poda:['poda quimica','poda','quimica','herbicida'],limpeza:['lavagem dos modulos','lavagem modulos','limpeza','lavagem','modulos','cleaning']};
    let currentGroup=null;const colGroup=[];
    for(let i=0;i<row1.length;i++){const v=norm(row1[i]);if(v){currentGroup=null;for(const[key,aliases]of Object.entries(GRUPO_MAP)){if(aliases.some(a=>v.includes(a)||a.includes(v))){currentGroup=key;break;}}}colGroup.push(currentGroup);}
    const SUB_MAP={};for(const tipo of['supressao','poda','limpeza'])SUB_MAP[tipo]={ultimaData:-1,proxData:-1,status:-1,qtd:-1};
    for(let i=0;i<row2.length;i++){const grupo=colGroup[i];if(!grupo||!SUB_MAP[grupo])continue;const sub=norm(row2[i]);if(sub.includes('ultima')||sub.includes('realiz'))SUB_MAP[grupo].ultimaData=i;else if(sub.includes('proxim')||sub.includes('next')||sub.includes('prox'))SUB_MAP[grupo].proxData=i;else if(sub.includes('status')||sub.includes('situac')||sub.includes('estado'))SUB_MAP[grupo].status=i;else if(sub.includes('quant')||sub.includes('qtd')||sub.includes('obs'))SUB_MAP[grupo].qtd=i;}
    const iCliente=0,iUsina=1;const novo={limpeza:[],supressao:[],poda:[]};
    const g=(cells,i)=>(i>=0&&i<cells.length)?cells[i].trim():'';
    allRows.slice(2).forEach((rawRow,rowIdx)=>{const cells=parseCSVLine(rawRow);const cliente=g(cells,iCliente);const usina=normalizeUsina(g(cells,iUsina));if(!usina)return;['supressao','poda','limpeza'].forEach(tipo=>{const m=SUB_MAP[tipo];novo[tipo].push({id:`${usina}_${tipo}_${rowIdx}`,cliente,usina,status:zelNormalizeStatus(g(cells,m.status)),previsto:'',realizado:zelFormatDate(g(cells,m.ultimaData)),prox:zelFormatDate(g(cells,m.proxData)),qtd:g(cells,m.qtd),obs:'',_fromSheet:true});});});
    const total=novo.limpeza.length+novo.supressao.length+novo.poda.length;if(total===0)throw new Error('Nenhuma usina mapeada');
    zelMergeEdits(novo);zelData=novo;_zelFromSheet=true;
    const now=new Date();const ts=now.toLocaleDateString('pt-BR')+' '+now.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
    if(badge)badge.className='source-badge live';if(label)label.textContent=`planilha · ${ts}`;
  }catch(err){
    console.warn('[Zeladorias] Erro:',err.message);
    try{const saved=localStorage.getItem('om_zeladorias');if(saved){zelData=Object.assign({limpeza:[],supressao:[],poda:[]},JSON.parse(saved));_zelFromSheet=false;if(badge)badge.className='source-badge error';if(label)label.textContent='cache local';}else{zelData=zelEmptyFromData();if(badge)badge.className='source-badge error';if(label)label.textContent='sem conexão';}}catch(e2){zelData={limpeza:[],supressao:[],poda:[]};}
  }finally{if(btn){btn.disabled=false;btn.classList.remove('spinning');}}
}

async function openZeladorias(){document.getElementById('zelOverlay').classList.add('open');await zelFetchSheet();zelRenderFull();}
function closeZeladorias(){document.getElementById('zelOverlay').classList.remove('open');}
function zelRenderFull(){
  const clientes=[...new Set([...zelData.limpeza,...zelData.supressao,...zelData.poda].map(r=>r.cliente).filter(Boolean))].sort();
  const sel=document.getElementById('zelFiltroCliente');const cur=sel.value;
  sel.innerHTML='<option value="">Todos os clientes</option>'+clientes.map(c=>`<option value="${esc(c)}" ${cur===c?'selected':''}>${esc(c)}</option>`).join('');
  zelRender();
}
function zelGetFiltered(){const rows=zelData[zelTab]||[];const cliente=document.getElementById('zelFiltroCliente').value.trim().toLowerCase();const status=document.getElementById('zelFiltroStatus').value.trim().toLowerCase();const search=document.getElementById('zelSearch').value.trim().toLowerCase();return rows.filter(r=>{if(cliente&&r.cliente.toLowerCase()!==cliente)return false;if(status&&r.status.toLowerCase()!==status)return false;if(search&&!r.usina.toLowerCase().includes(search)&&!r.cliente.toLowerCase().includes(search))return false;return true;});}
function zelRender(){
  const allRows=zelData[zelTab]||[];const rows=zelGetFiltered();
  Object.keys(ZEL_TIPOS).forEach(t=>{const el=document.getElementById('tabBadge'+t.charAt(0).toUpperCase()+t.slice(1));if(el)el.textContent=(zelData[t]||[]).length;});
  const total=allRows.length,concl=allRows.filter(r=>r.status==='Concluído').length,atrasado=allRows.filter(r=>r.status==='Atrasado').length,agend=allRows.filter(r=>r.status==='Agendado'||r.status==='Em Andamento').length;
  document.getElementById('zelKpis').innerHTML=[{num:total,label:'Usinas programadas',color:'var(--teal)'},{num:concl,label:'Concluídas',color:'var(--sla-ok)'},{num:agend,label:'Em Andamento / Agendadas',color:'var(--blue)'},{num:atrasado,label:'Com atraso',color:'var(--red)'}].map(k=>`<div class="zel-kpi" style="--kc:${k.color}"><div class="zk-num">${String(k.num).padStart(2,'0')}</div><div class="zk-label">${k.label}</div></div>`).join('');
  if(rows.length===0){document.getElementById('zelTableWrap').innerHTML=`<div class="zel-empty"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg><strong>Nenhum registro encontrado</strong>Ajuste os filtros ou adicione uma nova usina.</div>`;return;}
  const today=new Date();today.setHours(0,0,0,0);
  function parseDate(s){if(!s)return null;const p=s.split('/');if(p.length!==3)return null;return new Date(+p[2],+p[1]-1,+p[0]);}
  function isOverdue(row){if(row.status==='Concluído')return false;const d=parseDate(row.prox)||parseDate(row.previsto);return d&&d<today;}
  const rowsHtml=rows.map(row=>{const cc=clientColor(row.cliente);const stCls=ZEL_STATUS_CLASS[row.status]||'zs-pendente';const overdue=row.status==='Atrasado'||isOverdue(row);const stOpts=ZEL_STATUS.map(s=>`<option value="${esc(s)}" ${row.status===s?'selected':''}>${esc(s)}</option>`).join('');return`<tr data-id="${row.id}"><td class="zel-usina-cell"><span class="zel-cliente-tag" style="background:${cc.color}22;color:${cc.color};border:1px solid ${cc.color}44;">${esc(row.cliente)}</span>${esc(row.usina)}</td><td><label class="${stCls} zs-chip" style="cursor:pointer;"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="currentColor"/></svg><select class="zs-select" data-field="status" data-id="${row.id}">${stOpts}</select></label></td><td class="zel-date ${overdue?'overdue':''}">${esc(row.realizado||'—')}</td><td class="zel-date">${esc(row.prox||'—')}</td><td style="font-family:'Geist Mono',monospace;font-size:12px;color:var(--text-dim);">${esc(row.qtd||'—')}</td><td><input class="zel-obs-input" type="text" placeholder="Observação…" value="${esc(row.obs||'')}" data-field="obs" data-id="${row.id}" maxlength="120"></td><td style="text-align:center;"><button class="drawer-icon-btn" style="border-color:rgba(226,84,61,.25);color:var(--red);" data-del="${row.id}"><svg viewBox="0 0 24 24" style="width:13px;height:13px;stroke:currentColor;fill:none;stroke-width:2;"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/></svg></button></td></tr>`;}).join('');
  document.getElementById('zelTableWrap').innerHTML=`<table class="zel-table"><thead><tr><th>Usina</th><th>Status</th><th>Última Execução</th><th>Próx. Execução</th><th>Quantidade</th><th>Observações</th><th style="width:44px;"></th></tr></thead><tbody>${rowsHtml}</tbody></table>`;
  document.querySelectorAll('#zelTableWrap .zs-select').forEach(sel=>{sel.addEventListener('change',function(){const id=this.dataset.id;const row=(zelData[zelTab]||[]).find(r=>String(r.id)===String(id));if(row){row.status=this.value;if(_zelFromSheet)zelSaveEdit(row.usina,zelTab,'status',this.value);zelSave();zelRender();}});});
  document.querySelectorAll('#zelTableWrap .zel-obs-input').forEach(inp=>{inp.addEventListener('change',function(){const id=this.dataset.id;const row=(zelData[zelTab]||[]).find(r=>String(r.id)===String(id));if(row){row.obs=this.value;if(_zelFromSheet)zelSaveEdit(row.usina,zelTab,'obs',this.value);zelSave();}});});
  document.querySelectorAll('#zelTableWrap [data-del]').forEach(btn=>{btn.addEventListener('click',function(){const id=this.dataset.del;if(!confirm('Remover esta usina da programação?'))return;zelData[zelTab]=(zelData[zelTab]||[]).filter(r=>String(r.id)!==String(id));zelSave();zelRender();});});
}
function zelAddRow(){const usina=prompt('Nome da usina:');if(!usina)return;const cliente=prompt('Cliente (ex: THOPEN, RENOGRID):')||'';const previsto=prompt('Data prevista (DD/MM/AAAA):')||'';const prox=prompt('Próxima execução (DD/MM/AAAA):')||'';if(!zelData[zelTab])zelData[zelTab]=[];zelData[zelTab].push({id:Date.now()+Math.random(),cliente:cliente.toUpperCase().trim(),usina:usina.trim(),status:'Agendado',previsto:previsto.trim(),realizado:'',prox:prox.trim(),obs:''});zelSave();zelRender();showToast('Usina adicionada!',true);}
document.querySelectorAll('.zel-tab').forEach(tab=>{tab.addEventListener('click',()=>{zelTab=tab.dataset.tab;document.querySelectorAll('.zel-tab').forEach(t=>t.classList.remove('active'));tab.classList.add('active');zelRender();});});
['zelFiltroCliente','zelFiltroStatus','zelSearch'].forEach(id=>{const el=document.getElementById(id);if(el)el.addEventListener('input',zelRender);});
document.getElementById('processosBtn').addEventListener('click',openZeladorias);
document.getElementById('zelBack').addEventListener('click',closeZeladorias);
document.getElementById('zelRefreshBtn').addEventListener('click',async()=>{await zelFetchSheet();zelRenderFull();showToast('Dados de zeladorias atualizados!',true);});
document.getElementById('zelAddRow').addEventListener('click',zelAddRow);

/* ══ NOTIFICAÇÕES PUSH ══ */
const VAPID_PUBLIC_KEY = 'BPU55JogEEcV6GlCUONmzkVam8Tt9a0DuX3FYfn_ltgKc8p1fahQiE8v5RGECnMkSYEXMyUzOYBtslhUdiOJ6Jk';
const PUSH_SUBSCRIBE_URL = 'https://whatsapp-painel-falhas.onrender.com/push/subscribe';

let _swRegistration = null;

// Converte base64url para Uint8Array (necessário para VAPID)
function urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

async function initPush() {
  const btn   = document.getElementById('notifBtn');
  const label = document.getElementById('notifBtnLabel');

  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    if (btn) { btn.classList.add('disabled'); btn.title = 'Navegador não suporta push'; }
    return;
  }

  try {
    _swRegistration = await navigator.serviceWorker.register(
      'https://fred-alexandrino.github.io/PAINELDEFALHAS/sw.js',
      { scope: '/PAINELDEFALHAS/' }
    );

    // Atualiza UI conforme estado atual e adiciona listener de toggle
    await _atualizarEstadoNotif();
    if (btn) btn.addEventListener('click', _toggleNotificacoes);

  } catch(e) {
    console.error('[Push] Erro ao registrar SW:', e);
  }
}

async function _atualizarEstadoNotif() {
  const btn   = document.getElementById('notifBtn');
  const label = document.getElementById('notifBtnLabel');
  if (!_swRegistration) return;

  const sub = await _swRegistration.pushManager.getSubscription();
  const ativa = !!sub;

  if (btn) {
    btn.classList.toggle('enabled', ativa);
    btn.title = ativa ? 'Clique para desativar notificações' : 'Clique para ativar notificações';
  }
  if (label) label.textContent = ativa ? 'Notificações ativas' : 'Notificações';
}

async function _toggleNotificacoes() {
  const btn   = document.getElementById('notifBtn');
  const label = document.getElementById('notifBtnLabel');
  if (!_swRegistration) return;

  // Verifica estado atual
  const sub = await _swRegistration.pushManager.getSubscription();

  if (sub) {
    // ── DESATIVAR ──────────────────────────────────────────────
    try {
      await sub.unsubscribe();
      if (btn)   { btn.classList.remove('enabled'); btn.title = 'Clique para ativar notificações'; }
      if (label) label.textContent = 'Notificações';
      showToast('Notificações desativadas.', true);
      console.log('[Push] Subscription removida');
    } catch(e) {
      showToast('Erro ao desativar: ' + e.message, false);
    }
    return;
  }

  // ── ATIVAR ─────────────────────────────────────────────────
  try {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      showToast('Permissão de notificação negada.', false);
      return;
    }

    const newSub = await _swRegistration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    });

    const res = await fetch(PUSH_SUBSCRIBE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: newSub.toJSON() }),
    });

    if (res.ok) {
      if (btn)   { btn.classList.add('enabled'); btn.title = 'Clique para desativar notificações'; }
      if (label) label.textContent = 'Notificações ativas';
      showToast('Notificações ativadas! 🔔', true);
    } else {
      throw new Error('Servidor retornou ' + res.status);
    }
  } catch(e) {
    console.error('[Push] Erro ao ativar:', e);
    showToast('Erro ao ativar notificações: ' + e.message, false);
  }
}

// Push iniciado no initDashboard

/* ══ INICIALIZAÇÃO FINAL ══
   Processado AQUI — depois de todas as funções estarem definidas,
   incluindo fetchSheet(), initDashboard(), etc.
   Resolve o problema de F5 / reload que não carregava os dados.
══ */
if (window._pendingSession) {
  initDashboard(window._pendingSession);
  window._pendingSession = null;
}
</script>
</body>
</html>
