// Page markup, kept identical to the design prototype (v23) except where data is now live.
const MARKUP = String.raw`
<header class="top">
  <div class="wrap">
    <div class="brand">
      <svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true"><circle class="ring r2" cx="12" cy="12" r="10.5"></circle><circle class="ring r1" cx="12" cy="12" r="7.2"></circle><circle class="ring" cx="12.4" cy="11.8" r="4"></circle><circle cx="12.6" cy="11.6" r="1.6" fill="var(--ochre)"></circle></svg>
      FundedLabs
    </div>
    <nav class="tabs" role="tablist" aria-label="Sections">
      <button class="tab" role="tab" data-tab="explore" aria-selected="true">Explore</button>
      <button class="tab" role="tab" data-tab="outreach" aria-selected="false">Outreach<span class="badge" id="obadge">0</span></button>
      <button class="tab" role="tab" data-tab="pipe" aria-selected="false">Pipeline</button>
      <button class="tab" role="tab" data-tab="ask" aria-selected="false">Ask</button>
      <span class="ind" id="ind"></span>
    </nav>
    <span class="pill" id="dbpill" title="Every number on this page comes from the MySQL database through the API">MySQL</span>
    <button class="iconbtn" id="theme" aria-label="Switch light or dark theme">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"></path></svg>
    </button>
  </div>
</header>

<main class="wrap">

<!-- EXPLORE -->
<section class="view" id="v-explore" data-view="explore">
  <div class="xhead glass">
    <div class="xt">
      <span class="live"><i></i>Live</span>
      <h1><span class="w">Who's</span> <span class="w" style="animation-delay:.08s">funded,</span> <span class="w" style="animation-delay:.16s">and</span> <em><span class="w" style="animation-delay:.26s">for</span> <span class="w" style="animation-delay:.34s">how</span> <span class="w" style="animation-delay:.42s">long?</span></em></h1>
    </div>
    <div class="counter" aria-live="polite">
      <div><b id="cAwards">0</b><span>awards active today</span></div>
      <div><b id="cMoney">$0</b><span>in active awards</span></div>
    </div>
  </div>

  <div class="panel mapcard">
    <canvas id="usmap" role="img" aria-label="Live map of the United States. Arcs carry awards from NIH and NSF to university campuses, and states shade by active funding. Hover a state for its total, click to filter."></canvas>
    <div class="tip" id="mtip"></div>
    <div class="glegend" title="Bright arcs are the most recently started awards. Faint arcs are other active awards matching your filters. Campus dots sit inside the institution's state."><span><i style="background:var(--arc-nih)"></i>NIH</span><span><i style="background:var(--arc-nsf)"></i>NSF</span><span><i style="background:var(--campus)"></i>Campus</span><span><i style="background:var(--accent);border-radius:3px"></i>Darker = more funding · click a state</span></div>
    <ol class="feed" id="feed" aria-label="Awards arriving"></ol>
  </div>


  <button class="sidetoggle glass" id="sideToggle" type="button" aria-expanded="false" aria-controls="xside" aria-label="Show active awards"><span class="st-lbl"><b id="stCount">0</b> active awards <span aria-hidden="true">›</span></span><span class="st-x"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"></path></svg></span></button>
  <div class="xside glass" id="xside">
    <div class="controls">
      <label class="search" for="q">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="m20 20-4-4"></path></svg>
        <span class="sr">Search topic, professor or department</span>
        <input id="q" autocomplete="off" placeholder="Search titles, programs, departments">
      </label>
      <div class="seg sm" id="agencySeg" role="group" aria-label="Agency">
        <span class="thumb"></span>
        <button data-v="ALL" aria-pressed="true">Both</button>
        <button data-v="NIH" aria-pressed="false">NIH</button>
        <button data-v="NSF" aria-pressed="false">NSF</button>
      </div>
    </div>
    <div class="chips" id="uniChips" role="group" aria-label="University"></div>
    <section class="panel results" aria-label="Active awards">
      <div class="results-head"><h2 title="Ring = funding time left. Squares = fiscal years.">Active awards</h2><span class="rh-r"><button class="sqlbtn" id="sqlBtn" type="button" aria-expanded="false"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m8 7-5 5 5 5M16 7l5 5-5 5"></path></svg>SQL</button><span class="pager" id="pager"><span class="mono" id="pgTxt"></span><button type="button" id="pgPrev" aria-label="Previous page">‹</button><button type="button" id="pgNext" aria-label="Next page">›</button></span></span></div>
      <div class="lhead" aria-hidden="true"><span>Left</span><span>Award</span><span>Fiscal years</span><span>Total</span></div>
      <div id="rows"></div>
    </section>
    <section class="panel dock" aria-label="Query and top funded">
      <div class="dock-head">
        <div class="seg sm" id="dockSeg" role="group" aria-label="Show">
          <span class="thumb"></span>
          <button data-v="sql" aria-pressed="true">Live SQL</button>
          <button data-v="lead" aria-pressed="false">Top funded</button>
        </div>
        <span class="tag hot" id="access">range scan</span>
        <button class="dclose2" id="dockClose" type="button" aria-label="Close">×</button>
      </div>
      <div class="dock-body">
        <div class="sql" id="sqlpane"><pre class="code" id="sqlbox"></pre><div class="sql-foot"><span id="sqlrows"></span></div></div>
        <div id="leaders" hidden></div>
      </div>
    </section>
  </div>
</section>

<!-- OUTREACH -->
<section class="view" id="v-outreach" data-view="outreach" hidden>
  <div class="ohead">
    <div class="otitle"><h1>My outreach</h1><p>Click a step to move someone along. Each click runs an UPDATE on the outreach table.</p></div>
    <div class="fwrap"><div class="fstrip" id="funnel" aria-label="Outreach by stage"></div><div class="fbar" id="fbar" aria-hidden="true"></div></div>
    <div class="due"><span class="dot"></span><span id="dueTxt">0 follow-ups due</span></div>
  </div>
  <section class="panel olist" id="board" aria-label="People you are contacting"></section>
  <section class="panel nextup" aria-label="Funded, not contacted yet">
    <div class="results-head"><h2>Funded, not contacted yet</h2><span class="note" id="sugNote">active funding, longest runway first · Add runs an INSERT</span></div>
    <div class="pgrid" id="suggest"></div>
  </section>
</section>

<!-- PIPELINE -->
<section class="view" id="v-pipe" data-view="pipe" hidden>
  <div class="ohead">
    <div class="otitle"><h1>Data pipeline</h1><p>Every run is logged in ingest_run and ingest_step.</p></div>
    <div class="seg sm" id="runSeg" role="group" aria-label="Run">
      <span class="thumb"></span>
    </div>
    <button class="run" id="replay"><svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4l14 8-14 8z" fill="currentColor"></path></svg>Replay run</button>
  </div>
  <section class="panel pipe" aria-label="Steps"><div class="stages" id="stages"></div></section>
  <div class="pipegrid">
    <div class="log" id="log" aria-live="polite"><div class="cm">Press Replay run to stream this run's steps.</div></div>
    <section class="panel itabp" aria-label="ingest_step rows">
      <div class="results-head"><h2>ingest_step</h2><span class="note">what the app reads back after a run</span></div>
      <table class="itab"><thead><tr><th>step</th><th>status</th><th>rows in</th><th>rows out</th><th>time</th></tr></thead><tbody id="itab"></tbody></table>
      <p class="note" id="itabNote" style="margin:0;padding:10px 12px">Rows and times are read back from ingest_step.</p>
    </section>
  </div>
</section>

<!-- ASK -->
<section class="view" id="v-ask" data-view="ask" hidden>
  <div class="ask">
    <div><div class="eyebrow">Query workload</div><h1 style="margin-top:6px">Ask the database</h1><p class="note" id="askCtx" style="margin:4px 0 0"></p></div>
    <div class="thread" id="thread"></div>
    <div class="sugg" id="sugg"></div>
    <form class="askform" id="askForm">
      <label for="askIn" class="sr">Ask a question</label>
      <input id="askIn" autocomplete="off" placeholder="Ask about awards, professors or funding (matched to Q1 to Q8)">
      <button class="run" type="submit">Ask</button>
    </form>
  </div>
</section>
</main>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-labelledby="dName" aria-hidden="true"></aside>
<div class="toast" id="toast" role="status"><span class="tag hot">UPDATE</span><code id="toastCode"></code></div>
`;
export default MARKUP;
