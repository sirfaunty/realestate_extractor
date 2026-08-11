/* Capactive — universal job progress widget.
 *
 * Self-injecting floating pill (bottom-right) that polls /api/jobs/active
 * and shows any running extraction/generation job on EVERY page, so
 * navigating away from the page that started a job never loses sight of
 * it. Injected app-wide by an after_request hook in webapp.py; standalone
 * module pages (deliverables, portfolio, ...) get it for free.
 *
 * Defers to the richer inline banner on pages that extend base.html
 * (#active-job-banner) and stays quiet on /job/ status pages.
 */
(function () {
  if (document.getElementById('active-job-banner')) return;
  if (window.location.pathname.startsWith('/job/')) return;
  if (window.__capactiveProgress) return;
  window.__capactiveProgress = true;

  var el = document.createElement('div');
  el.id = 'cap-global-progress';
  el.style.cssText =
    'position:fixed;right:20px;bottom:20px;z-index:99999;display:none;' +
    'min-width:260px;max-width:360px;background:#1a1f2e;color:#e6edf3;' +
    'border:1px solid #2d3548;border-radius:10px;padding:12px 16px;' +
    'font:12.5px/1.45 Inter,system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;' +
    'box-shadow:0 6px 24px rgba(0,0,0,.45);cursor:default;';
  el.innerHTML =
    '<div style="display:flex;align-items:center;gap:10px">' +
    '  <span id="cap-gp-icon" style="flex:0 0 auto;width:14px;height:14px;' +
    '    border:2px solid #2d3548;border-top-color:#3A8FD4;border-radius:50%;' +
    '    animation:capspin .8s linear infinite"></span>' +
    '  <div style="flex:1;min-width:0">' +
    '    <div id="cap-gp-label" style="font-weight:600;white-space:nowrap;' +
    '      overflow:hidden;text-overflow:ellipsis"></div>' +
    '    <div id="cap-gp-detail" style="color:#8b949e;white-space:nowrap;' +
    '      overflow:hidden;text-overflow:ellipsis"></div>' +
    '  </div>' +
    '  <a id="cap-gp-close" style="color:#8b949e;text-decoration:none;' +
    '    cursor:pointer;padding:0 2px">&times;</a>' +
    '</div>' +
    '<div style="margin-top:8px;height:3px;background:#2d3548;border-radius:2px">' +
    '  <div id="cap-gp-fill" style="height:3px;width:0;background:#3A8FD4;' +
    '    border-radius:2px;transition:width .5s"></div></div>';
  var style = document.createElement('style');
  style.textContent = '@keyframes capspin{to{transform:rotate(360deg)}}';
  document.head.appendChild(style);
  document.body.appendChild(el);

  var label = document.getElementById('cap-gp-label');
  var detail = document.getElementById('cap-gp-detail');
  var fill = document.getElementById('cap-gp-fill');
  var icon = document.getElementById('cap-gp-icon');
  var dismissedUntilNextJob = false;
  var lastJob = null;        // {id, type, filename} of most recent active job
  var doneTimer = null;

  document.getElementById('cap-gp-close').onclick = function () {
    el.style.display = 'none';
    dismissedUntilNextJob = true;
  };

  var STEPS = { ingesting: 'Ingesting', classifying: 'Classifying',
                extracting: 'Extracting', storing: 'Storing',
                rendering: 'Rendering document', complete: 'Done' };

  function spinner(on, color) {
    if (on) {
      icon.style.cssText = 'flex:0 0 auto;width:14px;height:14px;' +
        'border:2px solid #2d3548;border-top-color:#3A8FD4;' +
        'border-radius:50%;animation:capspin .8s linear infinite';
      icon.textContent = '';
    } else {
      icon.style.cssText = 'flex:0 0 auto;font-weight:700;color:' + color;
      icon.textContent = color === '#10B981' ? '✓' : '✗';
    }
  }

  function showDone(job) {
    // job just left the active list — fetch its final state for an
    // accurate finished/failed message + landing link
    fetch('/api/job/' + job.id)
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (fin) {
        var failed = fin && (fin.status === 'failed' || fin.error);
        spinner(false, failed ? '#f85149' : '#10B981');
        var dest = job.type === 'deliverable' ? '/deliverables' : '/documents';
        var destLabel = job.type === 'deliverable'
          ? 'View deliverables' : 'View documents';
        label.textContent = (failed ? 'Failed: ' : 'Finished: ') +
          (job.filename || 'job');
        detail.innerHTML = failed
          ? ((fin && fin.error) ? String(fin.error).slice(0, 120) : 'See job log')
          : '<a href="' + dest + '" style="color:#3A8FD4;text-decoration:none">' +
            destLabel + ' &rarr;</a>';
        fill.style.width = '100%';
        fill.style.background = failed ? '#f85149' : '#10B981';
        el.style.display = '';
        if (doneTimer) clearTimeout(doneTimer);
        doneTimer = setTimeout(function () { el.style.display = 'none'; }, 12000);
      });
  }

  function poll() {
    fetch('/api/jobs/active')
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (active) {
        if (!active.length) {
          if (lastJob) { var j = lastJob; lastJob = null;
                         dismissedUntilNextJob = false; showDone(j); }
          return;
        }
        if (doneTimer) { clearTimeout(doneTimer); doneTimer = null; }
        var processing = active.filter(function (j) { return j.status === 'processing'; });
        var queued = active.filter(function (j) { return j.status === 'queued'; });
        var job = processing[0] || queued[0];
        lastJob = { id: job.id, type: job.type, filename: job.filename };
        if (dismissedUntilNextJob) return;

        spinner(true);
        fill.style.background = '#3A8FD4';
        var pct = job.total ? Math.round(job.progress / job.total * 100) : 0;
        if (job.status === 'queued') {
          label.textContent = 'Queued: ' + (job.filename || 'job');
          detail.textContent = 'Waiting for current job to finish';
          fill.style.width = '0%';
        } else if (job.type === 'deliverable') {
          label.textContent = 'Generating: ' + (job.filename || 'document');
          detail.textContent = job.step_detail || 'Rendering document';
          fill.style.width = '100%';
        } else if (job.type === 'single') {
          label.textContent = 'Processing: ' + (job.filename || 'document');
          detail.textContent = STEPS[job.step] || job.step_detail || '';
          fill.style.width = pct + '%';
        } else {
          label.textContent = 'Batch: ' + job.progress + ' / ' + job.total + ' files';
          detail.textContent = STEPS[job.step] || job.step_detail || '';
          fill.style.width = pct + '%';
        }
        if (queued.length && processing.length)
          detail.textContent += '  ·  ' + queued.length + ' more queued';
        el.style.display = '';
      })
      .catch(function () { /* server restarting — try again next tick */ });
  }

  poll();
  setInterval(poll, 3000);
})();
