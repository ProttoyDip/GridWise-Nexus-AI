/* Operational summaries only: no fabricated progress, confidence or solver counts. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const num = (v, digits = 2) => Number.isFinite(v) ? v.toLocaleString(undefined, {maximumFractionDigits: digits}) : '—';
  const pct = v => num(v, 1) + '%';
  const node = (tag, text, className) => {const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e;};
  const roles = ['interpretation', 'interpretation', 'safety', 'optimization', 'explanation'];
  let lastResult = null;
  let simulationBusy = false;

  function selectTab(tab, focus = false) {
    document.querySelectorAll('.tab').forEach(t => {
      const selected = t === tab;
      t.classList.toggle('active', selected); t.setAttribute('aria-selected', String(selected)); t.tabIndex = selected ? 0 : -1;
      $('panel-' + t.dataset.tab).classList.toggle('active', selected);
    });
    if (focus) tab.focus();
    if (tab.dataset.tab === 'dashboard' && lastResult && window.Plotly) requestAnimationFrame(() => ['chart-battery','chart-grid'].forEach(id => Plotly.Plots.resize($(id))));
  }
  const tabs = [...document.querySelectorAll('.tab')];
  tabs.forEach((tab, index) => {
    tab.id = 'tab-' + tab.dataset.tab; tab.setAttribute('aria-controls', 'panel-' + tab.dataset.tab);
    $('panel-' + tab.dataset.tab).setAttribute('aria-labelledby', tab.id);
    tab.tabIndex = index === 0 ? 0 : -1;
    tab.addEventListener('click', () => selectTab(tab));
    tab.addEventListener('keydown', event => {
      const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']; if (!keys.includes(event.key)) return;
      event.preventDefault();
      const target = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      selectTab(tabs[target], true);
    });
  });
  document.querySelectorAll('.chip').forEach(chip => chip.addEventListener('click', () => {$('note').value = chip.dataset.example; $('note').focus();}));

  function agent(id, label, failed = false) {
    const badge = document.querySelector('[data-agent="' + id + '"]');
    badge.textContent = label; badge.classList.toggle('idle', label === 'Idle'); badge.classList.toggle('failed', failed);
  }
  function progress(event) {
    const step = document.querySelector('#timeline [data-stage="' + event.stage + '"]');
    if (!step) return;
    step.classList.toggle('done', event.state === 'completed'); step.classList.toggle('active', event.state === 'running'); step.classList.remove('failed');
    step.querySelector('.ico').textContent = event.state === 'completed' ? '✓' : event.stage;
    $('working-detail').textContent = step.querySelector('.title').textContent + (event.state === 'completed' ? ' · complete' : '…');
    agent(roles[event.stage - 1], event.state === 'running' ? 'Working' : 'Checked');
  }

  async function request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw Error(typeof data.detail === 'string' ? data.detail : 'Request failed (' + response.status + ')');
    }
    return response.json();
  }
  function statusPill(id, label, available) {$(id).className = 'pill ' + (available ? 'ok' : 'warn'); $(label).textContent = available;}
  async function loadStatus() {
    try {
      const status = await request('/system/status');
      const models = status.model_availability;
      const configured = models.configured && models.models.some(m => !m.circuit_open);
      $('pill-llm').className = 'pill ' + (configured ? 'ok' : 'warn'); $('llm-text').textContent = configured ? 'Configured' : 'Unavailable';
      $('pill-llm').title = 'Provider configuration and circuit status; connectivity is checked during requests.';
      const ready = status.optimizer_status.available;
      $('pill-opt').className = 'pill ' + (ready ? 'ok' : 'bad'); $('opt-text').textContent = ready ? 'Ready' : 'Unavailable';
      $('pill-grid').className = 'pill ' + (ready ? 'ok' : 'warn'); $('grid-text').textContent = ready ? 'Ready' : 'Unavailable';
    } catch {
      ['llm-text', 'opt-text', 'grid-text'].forEach(id => $(id).textContent = 'Unknown');
    }
  }
  async function loadBenchmark() {
    try {
      const {benchmark: b, runtime: r} = await request('/demo/metrics');
      $('bench').replaceChildren();
      for (const text of ['Models tested: ' + (b.models_tested ?? '—'), 'Best interpreter: ' + (b.best_interpreter ?? 'No eligible model'), 'Public-sample accuracy: ' + (Number.isFinite(b.accuracy) ? pct(b.accuracy * 100) : '—'), 'Mean latency: ' + num(b.mean_latency_seconds) + ' s', b.scope ?? 'No report', 'Measured: ' + (b.generated_at ?? '—'), 'Runtime: ' + r.total_requests + ' requests · ' + r.total_fallbacks + ' fallback directives']) $('bench').append(node('div', text));
    } catch(e) {$('bench').textContent = e.message;}
  }
  function directives(cards) {
    $('directive-count').textContent = cards.filter(c => c.applies).length + ' active';
    $('directives').replaceChildren();
    for (const card of cards) {
      const element = node('div', undefined, 'd-card');
      const header = node('div', undefined, 'head'); header.append(node('span', card.icon, 'ic'), node('span', card.title, 'title'));
      element.append(header);
      for (const summary of card.summary) element.append(node('div', summary, 'meta'));
      element.append(node('div', card.applies ? '✓ Applied to verified schedule' : 'No operating constraint applied', 'muted'), node('p', card.explanation, 'muted'));
      $('directives').append(element);
    }
  }
  function comparisons(data) {
    const {before, after, savings} = data;
    for (const [name, unit, field] of [['grid', 'kWh', 'total_grid_kwh'], ['cost', 'BDT', 'total_cost_bdt']]) {
      const max = Math.max(before[field], after[field], 1);
      for (const [side, plan] of [['before', before], ['after', after]]) {
        $('cmp-' + name + '-' + side).textContent = num(plan[field]) + ' ' + unit;
        $('bar-' + name + '-' + side).style.width = 100 * plan[field] / max + '%';
      }
    }
    $('cost-before').textContent = num(before.total_cost_bdt) + ' BDT'; $('cost-after').textContent = num(after.total_cost_bdt) + ' BDT';
    $('cost-before-delta').textContent = num(before.total_grid_kwh) + ' kWh grid'; $('cost-after-delta').textContent = num(after.total_grid_kwh) + ' kWh grid';
    const increased = savings.bdt < -1e-5;
    $('savings-label').textContent = increased ? 'Additional constraint cost' : 'Savings vs reference';
    $('savings-value').textContent = num(Math.abs(savings.bdt)) + ' BDT';
    $('savings-delta').textContent = pct(Math.abs(savings.pct)) + (increased ? ' higher than reference' : ' below reference');
    $('savings-value').style.color = increased ? 'var(--amber)' : 'var(--green)';
    const peakDelta = before.peak_grid_kwh - after.peak_grid_kwh;
    $('results-summary').textContent = (peakDelta >= 0 ? 'Peak reduction: ' : 'Peak increase: ') + num(Math.abs(peakDelta)) + ' kWh';
    ['results', 'compare-card'].forEach(id => $(id).classList.remove('hidden'));
  }
  function explanation(data) {
    const e = data.explanation;
    $('rationale').replaceChildren();
    for (const reason of [...e.battery_reasons, ...e.constraint_explanations, e.end_of_day_explanation, e.cost_explanation]) $('rationale').append(node('li', reason));
    $('rationale-card').classList.remove('hidden');
    $('insights').classList.remove('empty'); $('insights').replaceChildren(node('h3', 'Last verified strategy'), node('p', data.after.summary), node('p', e.cost_explanation));
    for (const c of data.after.directive_cards) $('insights').append(node('h4', c.title), node('p', c.explanation));
    const table = node('table'); table.className = 'schedule-table';
    const caption = node('caption', 'Verified 24-hour schedule'); table.append(caption);
    const head = node('thead'), heading = node('tr');
    for (const text of ['Hour', 'Grid kWh', 'Battery action', 'Battery kWh', 'Energy after kWh']) {const cell = node('th', text); cell.scope = 'col'; heading.append(cell);} head.append(heading); table.append(head);
    const body = node('tbody');
    for (const h of data.after.hourly_plan) {const row = node('tr'); for (const value of [String(h.hour).padStart(2,'0') + ':00', num(h.grid_kwh), h.battery_action, num(h.battery_kwh), num(h.battery_energy_after_kwh)]) row.append(node('td', value)); body.append(row);} table.append(body);
    const wrapper = node('div', undefined, 'schedule-scroll'); wrapper.append(table); $('insights').append(wrapper);
  }
  function charts(data) {
    if (!window.Plotly) {['chart-battery','chart-grid'].forEach(id => $(id).textContent = 'Chart library unavailable. View the schedule in AI Insights.'); return;}
    const plan = data.after.hourly_plan, capacity = data.battery.capacity_kwh;
    const x = plan.map(h => h.hour + 1);
    const floor = plan.map(h => {
      let reserve = data.battery.minimum_energy_kwh;
      for (const d of data.after.directives) if (d.directive_type === 'minimum_battery_reserve' && d.structured_adjustment.hours.includes(h.hour)) reserve = Math.max(reserve, d.structured_adjustment.minimum_energy_kwh);
      return reserve / capacity * 100;
    });
    const layout = {paper_bgcolor:'transparent', plot_bgcolor:'transparent', font:{color:'#8aa0b8',size:11}, margin:{l:44,r:12,t:20,b:58}, legend:{orientation:'h',y:-.24,font:{size:10}}, hovermode:'x unified', xaxis:{title:{text:'Hour end'},range:[0,24],dtick:6,gridcolor:'rgba(0,168,255,.1)'}, yaxis:{gridcolor:'rgba(0,168,255,.1)'}, autosize:true};
    const battery = [{type:'scatter', mode:'lines', name:'Battery %', x:[0,...x], y:[data.battery.initial_energy_kwh / capacity * 100,...plan.map(h=>h.battery_energy_after_kwh/capacity*100)], line:{color:'#00d084',shape:'hv'},fill:'tozeroy',fillcolor:'rgba(0,208,132,.07)'}, {type:'scatter',mode:'lines',name:'Required reserve',x:[0,...x],y:[data.battery.minimum_energy_kwh/capacity*100,...floor],line:{color:'#ffb547',dash:'dash',shape:'hv'}}];
    for (const [action, color, symbol] of [['charge','#00a8ff','triangle-up'],['discharge','#ffb547','triangle-down']]) {const entries=plan.filter(h=>h.battery_action===action); battery.push({type:'scatter',mode:'markers',name:action,x:entries.map(h=>h.hour+1),y:entries.map(h=>h.battery_energy_after_kwh/capacity*100),marker:{color,size:7,symbol},customdata:entries.map(h=>h.battery_kwh),hovertemplate:action+' %{customdata:.2f} kWh<extra></extra>'});}
    const config = {responsive:true,displaylogo:false,displayModeBar:false};
    Plotly.react($('chart-battery'), battery, {...layout,yaxis:{...layout.yaxis,range:[0,105],ticksuffix:'%'}},config).catch(()=>$('chart-battery').textContent='Battery chart unavailable. View AI Insights.');
    Plotly.react($('chart-grid'), [{type:'scatter',mode:'lines',name:'Before AI',x:plan.map(h=>h.hour),y:data.before.hourly_plan.map(h=>h.grid_kwh),line:{color:'#8aa0b8',dash:'dot'}},{type:'bar',name:'After AI',x:plan.map(h=>h.hour),y:plan.map(h=>h.grid_kwh),marker:{color:plan.map(h=>h.grid_kwh>=data.after.peak_grid_kwh-1e-5?'#ffb547':'#00a8ff')}}], {...layout,xaxis:{...layout.xaxis,title:{text:'Hour'},range:[-.5,23.5]},yaxis:{...layout.yaxis,title:{text:'Grid kWh'},rangemode:'tozero'}},config).catch(()=>$('chart-grid').textContent='Grid chart unavailable. View AI Insights.');
  }
  function result(data) {
    lastResult = data; directives(data.after.directive_cards); comparisons(data); explanation(data); charts(data);
    for (const [id,label] of [['interpretation','Interpreted'],['safety','Verified'],['optimization','Completed'],['explanation','Ready']]) agent(id,label);
    $('status').textContent = 'Strategy verified · ' + data.after.directive_cards.filter(c=>c.applies).length + ' active directives · 24 hours';
  }
  async function run() {
    const notes = $('note').value.split('\n').map(s=>s.trim()).filter(Boolean);
    if (!notes.length || notes.length > 3 || notes.some(n=>n.length > 2000)) {$('status').textContent='Enter one to three instructions, each under 2,000 characters.'; $('note').focus(); return;}
    $('optimize').disabled=true; $('working').hidden=false; $('status').textContent='AI agents working…'; $('main').setAttribute('aria-busy','true');
    document.querySelectorAll('#timeline .step').forEach(step=>{step.classList.remove('done','active','failed');step.querySelector('.ico').textContent=step.dataset.stage;});
    roles.forEach(id=>agent(id,'Idle'));
    const controller = new AbortController(), timer=setTimeout(()=>controller.abort(),180000);
    let reader;
    try {
      const response=await fetch('/demo/run-optimization-stream',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({operator_notes:notes}),signal:controller.signal});
      if (!response.ok) throw Error('Request failed ('+response.status+'). Try again shortly.');
      reader=response.body.getReader(); const decoder=new TextDecoder(); let buffer='', received=false;
      function event(line) {if(!line.trim())return; const value=JSON.parse(line); if(value.event==='progress')progress(value); else if(value.event==='error')throw Error(value.detail); else if(value.event==='result'){result(value.data);received=true;}}
      while(true){const {done,value}=await reader.read();buffer+=decoder.decode(value,{stream:!done});let index;while((index=buffer.indexOf('\n'))>=0){event(buffer.slice(0,index));buffer=buffer.slice(index+1);}if(done){event(buffer);break;}}
      if(!received)throw Error('Connection closed before a verified strategy was returned.');
    } catch(e) {
      $('status').textContent=e.name==='AbortError'?'Request timed out. Check provider status and try again.':'Error: '+e.message;
      document.querySelectorAll('#timeline .step.active').forEach(step=>{step.classList.remove('active');step.classList.add('failed');step.querySelector('.ico').textContent='!';});
      roles.forEach(id=>agent(id,'Stopped',true));
    } finally {clearTimeout(timer);if(reader)await reader.cancel().catch(()=>{});$('optimize').disabled=false;$('working').hidden=true;$('main').setAttribute('aria-busy','false');loadStatus();loadBenchmark();}
  }
  async function simulate() {
    if(simulationBusy)return;simulationBusy=true;selectTab(tabs[1]);['emergency','sim-emergency'].forEach(id=>$(id).disabled=true);$('sim-status').textContent='Optimizing and verifying possible futures…';
    try {
      const data=await request('/demo/simulate-emergency',{method:'POST'});$('sim-outcomes').replaceChildren();
      for(const o of data.outcomes){const row=node('tr');for(const value of [o.assumption,o.feasible?num(o.cost_bdt):'Infeasible',o.feasible?num(o.grid_kwh):'—',o.feasible?num(o.charge_kwh)+' / '+num(o.discharge_kwh):'—'])row.append(node('td',value));$('sim-outcomes').append(row);}
      $('sim-risk').textContent=data.risk.recommendation+' Cost spread: '+num(data.risk.cost_spread_bdt)+' BDT. Conditional future optima; no probabilities assigned.';
      $('sim-status').textContent=data.outcomes.length+' hypothetical futures evaluated and checked';
    }catch(e){$('sim-status').textContent='Error: '+e.message;}finally{simulationBusy=false;['emergency','sim-emergency'].forEach(id=>$(id).disabled=false);}
  }
  $('optimize').addEventListener('click',run);$('emergency').addEventListener('click',simulate);$('sim-emergency').addEventListener('click',simulate);
  loadStatus();loadBenchmark();setInterval(loadStatus,30000);
})();
