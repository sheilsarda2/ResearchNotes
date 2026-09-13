/* Show effort in the Jobs table (one row per job) and on the job heading. */
(function () {
  const STYLE_ID = "harbor-effort-style";
  const HEADING_ID = "harbor-effort-heading";

  function effortFromKwargs(kwargs) {
    if (!kwargs || typeof kwargs !== "object") return null;
    return kwargs.reasoning_effort || kwargs.effort || kwargs.reasoningEffort || null;
  }

  function effortsFromConfig(config) {
    const seen = [];
    for (const agent of config.agents || []) {
      const effort = effortFromKwargs(agent.kwargs) || "high";
      const note = effortFromKwargs(agent.kwargs) ? "" : " (API default)";
      const model = (agent.model_name || "").split("/").pop() || agent.name || "model";
      const label = `${model} × ${effort}${note}`;
      if (!seen.includes(label)) seen.push(label);
    }
    return seen;
  }

  function parseRoute(pathname) {
    const parts = pathname.split("/").filter(Boolean);
    if (parts.length === 0) return { page: "jobs" };
    if (parts[0] !== "jobs") return null;
    if (!parts[1]) return { page: "jobs" };
    return { page: "job", jobName: decodeURIComponent(parts[1]) };
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      .harbor-effort-cell { color: var(--muted-foreground, #666); font-variant-numeric: tabular-nums; }
      .harbor-effort-heading {
        margin: 0.25rem 0 0.75rem;
        font: 500 13px/1.3 var(--font-sans, ui-sans-serif, system-ui, sans-serif);
        color: var(--muted-foreground, #555);
      }
    `;
    document.head.appendChild(style);
  }

  function decorateJobsTable(jobEfforts) {
    const table = document.querySelector("table");
    if (!table) return;
    const headerRow = table.querySelector("thead tr") || table.querySelector("tr");
    if (!headerRow) return;
    headerRow.querySelectorAll("[data-harbor-effort-col]").forEach((el) => el.remove());
    const th = document.createElement("th");
    th.dataset.harborEffortCol = "1";
    th.textContent = "Effort";
    const jobNameTh = [...headerRow.children].find((el) => el.textContent.trim() === "Job Name");
    if (jobNameTh) jobNameTh.insertAdjacentElement("afterend", th);
    else headerRow.appendChild(th);

    table.querySelectorAll("tbody tr").forEach((row) => {
      row.querySelectorAll("[data-harbor-effort-cell]").forEach((el) => el.remove());
      const jobName = (row.querySelectorAll("td")[1]?.innerText || "").trim();
      if (!jobName) return;
      const td = document.createElement("td");
      td.dataset.harborEffortCell = "1";
      td.className = "harbor-effort-cell";
      td.textContent = (jobEfforts[jobName] || []).join(", ") || "—";
      const nameTd = row.querySelectorAll("td")[1];
      if (nameTd) nameTd.insertAdjacentElement("afterend", td);
      else row.appendChild(td);
    });
  }

  function decorateJobHeading(labels) {
    const heading = document.querySelector("h1");
    if (!heading) return;
    let line = document.getElementById(HEADING_ID);
    if (!labels.length) {
      if (line) line.remove();
      return;
    }
    if (!line) {
      line = document.createElement("p");
      line.id = HEADING_ID;
      line.className = "harbor-effort-heading";
      heading.insertAdjacentElement("afterend", line);
    }
    line.textContent = "Effort: " + labels.join(", ");
  }

  async function refresh() {
    ensureStyle();
    const oldChips = document.getElementById("harbor-effort-badge");
    if (oldChips) oldChips.remove();
    const route = parseRoute(location.pathname);
    if (!route) {
      decorateJobHeading([]);
      return;
    }
    if (route.page === "jobs") {
      decorateJobHeading([]);
      try {
        const res = await fetch("/api/jobs?page=1&page_size=100");
        if (!res.ok) return;
        const data = await res.json();
        const jobs = data.items || data.jobs || data || [];
        const jobEfforts = {};
        await Promise.all(
          (Array.isArray(jobs) ? jobs : []).map(async (job) => {
            const name = job.name || job.job_name;
            if (!name) return;
            const cfgRes = await fetch(`/api/jobs/${encodeURIComponent(name)}/config`);
            if (!cfgRes.ok) return;
            jobEfforts[name] = effortsFromConfig(await cfgRes.json());
          })
        );
        decorateJobsTable(jobEfforts);
      } catch {
        /* ignore */
      }
      return;
    }
    try {
      const res = await fetch(`/api/jobs/${encodeURIComponent(route.jobName)}/config`);
      if (!res.ok) return;
      decorateJobHeading(effortsFromConfig(await res.json()));
    } catch {
      /* ignore */
    }
  }

  const _push = history.pushState;
  history.pushState = function () {
    _push.apply(this, arguments);
    setTimeout(refresh, 50);
  };
  const _replace = history.replaceState;
  history.replaceState = function () {
    _replace.apply(this, arguments);
    setTimeout(refresh, 50);
  };
  window.addEventListener("popstate", () => setTimeout(refresh, 50));
  setInterval(refresh, 2000);
  refresh();
})();
