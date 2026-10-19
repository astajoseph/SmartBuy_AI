"""Time-series shape detection, vendored from data/extract_dashboard_timeseries.py.

Lines below are copied unchanged from that script (value helpers, network-JSON shape
detectors and the in-page CHART_JS reader for Highcharts / Chart.js / Plotly / ECharts /
ApexCharts), so the SmartBuy collector and the standalone script read charts identically.
Only the imports and this header were added. MIN_POINTS is lowered to 2 by default because
price trackers often have short histories.
"""
import json
import re
from datetime import datetime, timezone

MIN_POINTS = 2

TIME_KEYS = [
    "timestamp", "time", "ts", "datetime", "date_time", "date", "t", "x",
    "period", "bucket", "time_bucket", "key_as_string", "key", "start_time",
    "starttime", "start", "created_at", "day", "week", "month", "year",
]
TIME_SET = set(TIME_KEYS)
ARRAY_TIME_SET = TIME_SET | {"timestamps", "times", "dates", "labels", "index", "xs", "categories"}
NAME_KEYS = ["name", "target", "label", "legend", "title", "alias", "displayName", "metric", "series", "refId"]
TIME_SUFFIXES = ("time", "date", "timestamp", "_at", "_ts")


# ----------------------------------------------------------------------------
# Value helpers
# ----------------------------------------------------------------------------

def to_iso(v):
    """Return an ISO-8601 UTC string if v looks like a timestamp, else None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        # seconds, milliseconds, microseconds, nanoseconds (years ~2001-2103)
        for lo, hi, div in ((1e9, 4.2e9, 1), (1e12, 4.2e12, 1e3), (1e15, 4.2e15, 1e6), (1e18, 4.2e18, 1e9)):
            if lo <= x < hi:
                try:
                    return datetime.fromtimestamp(x / div, tz=timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    return None
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return to_iso(float(s))
        if not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", s):
            return None
        s2 = s[:10].replace("/", "-") + s[10:]
        s2 = s2.replace("Z", "+00:00").replace(" UTC", "+00:00")
        for cand in (s2, s2[:26], s2[:19], s2[:10]):
            try:
                dt = datetime.fromisoformat(cand)
                break
            except ValueError:
                continue
        else:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return None


def num(v):
    """Return v as float if numeric (numeric strings included), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    if isinstance(v, dict) and "value" in v:  # Elasticsearch-style {"value": 3}
        return num(v["value"])
    return None


def sample(lst, n=200):
    if len(lst) <= n:
        return lst
    step = len(lst) / n
    return [lst[int(i * step)] for i in range(n)]


def frac(items, pred):
    if not items:
        return 0.0
    hits = 0
    for e in items:
        try:
            if pred(e):
                hits += 1
        except Exception:
            pass
    return hits / len(items)


def is_num_or_null(e):
    return e is None or num(e) is not None


def make_series(name, pairs):
    """pairs: iterable of (x_raw, y_raw). Returns a series dict or None."""
    points = [(x, to_iso(x), num(y)) for x, y in pairs]
    if len(points) < MIN_POINTS or not any(p[2] is not None for p in points):
        return None
    return {"name": name or "", "points": points}


def labels_str(d):
    return ",".join(f'{a}="{b}"' for a, b in d.items())


# ----------------------------------------------------------------------------
# Shape detectors for network JSON
# ----------------------------------------------------------------------------

def name_hint(d, parent):
    for k in NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if k == "metric" and isinstance(v, dict) and v:  # Prometheus
            base = str(v.get("__name__", ""))
            rest = {a: b for a, b in v.items() if a != "__name__"}
            return f"{base}{{{labels_str(rest)}}}" if rest else base
    return parent


def from_pairs(lst, hint):
    """[[t, v], ...], [[t, o, h, l, c], ...] or Graphite-style [[v, t], ...]."""
    if len(lst) < MIN_POINTS:
        return None
    s = sample(lst)
    if frac(s, lambda e: isinstance(e, (list, tuple)) and len(e) >= 2) < 0.95:
        return None
    rows = [e for e in lst if isinstance(e, (list, tuple)) and len(e) >= 2]
    width = min(len(e) for e in sample(rows))
    s = sample(rows)
    if frac(s, lambda e: to_iso(e[0]) is not None) >= 0.8 and frac(s, lambda e: is_num_or_null(e[1])) >= 0.8:
        t_idx, v_idxs = 0, list(range(1, width))
    elif width == 2 and frac(s, lambda e: to_iso(e[1]) is not None) >= 0.8 and frac(s, lambda e: is_num_or_null(e[0])) >= 0.8:
        t_idx, v_idxs = 1, [0]
    else:
        return None
    out = []
    for vi in v_idxs:
        nm = hint or ""
        if len(v_idxs) > 1:
            nm = f"{nm} [col{vi}]".strip()
        sr = make_series(nm, ((e[t_idx], e[vi] if len(e) > vi else None) for e in rows))
        if sr:
            out.append(sr)
    return out or None


def time_key_candidates(keys):
    keys = list(keys)
    ranked = [k for k in keys if k.lower() in TIME_SET]
    ranked.sort(key=lambda k: TIME_KEYS.index(k.lower()))
    ranked += [k for k in keys if k not in ranked and k.lower().endswith(TIME_SUFFIXES)]
    return ranked


def from_records(lst, hint):
    """[{"time": ..., "a": 1, "b": 2}, ...] -> one series per numeric column."""
    if len(lst) < MIN_POINTS:
        return None
    s = sample(lst)
    if frac(s, lambda e: isinstance(e, dict)) < 0.95:
        return None
    recs = [e for e in lst if isinstance(e, dict)]
    s = sample(recs)
    all_keys = []
    for e in s:
        for k in e:
            if k not in all_keys:
                all_keys.append(k)

    tkey = None
    time_like = set()
    for k in time_key_candidates(all_keys):
        if frac(s, lambda e: to_iso(e.get(k)) is not None) >= 0.8:
            time_like.add(k)
            if tkey is None:
                tkey = k
    if tkey is None:
        return None

    value_keys, group_keys = [], []
    for k in all_keys:
        if k in time_like:
            continue
        vals = [e.get(k) for e in s if e.get(k) is not None]
        if not vals:
            continue
        if frac(vals, lambda v: num(v) is not None) >= 0.8:
            value_keys.append(k)
        elif all(isinstance(v, str) for v in vals):
            uniq = {e.get(k) for e in recs}
            if 1 < len(uniq) <= 100 and len(uniq) < len(recs) / 2:
                group_keys.append(k)
    if not value_keys:
        return None

    gkey = group_keys[0] if len(group_keys) == 1 else None
    groups = {}
    for e in recs:
        g = e.get(gkey) if gkey else None
        groups.setdefault(g, []).append(e)

    out = []
    for g, rows in groups.items():
        for vk in value_keys:
            parts = [p for p in (hint, f"{gkey}={g}" if gkey else None, vk) if p]
            sr = make_series(" / ".join(parts), ((r.get(tkey), r.get(vk)) for r in rows))
            if sr:
                out.append(sr)
    return out or None


def from_parallel(d, hint):
    """{"timestamps": [...], "cpu": [...]} or {"labels": [...], "datasets": [{"data": [...]}]}."""
    lists = {k: v for k, v in d.items() if isinstance(v, list) and len(v) >= MIN_POINTS}
    if not lists:
        return None
    tkey = None
    for k, v in lists.items():
        kl = k.lower()
        if (kl in ARRAY_TIME_SET or kl.endswith(TIME_SUFFIXES)) and frac(sample(v), lambda e: to_iso(e) is not None) >= 0.8:
            tkey = k
            break
    if tkey is None:
        return None
    tl = lists[tkey]
    out = []
    for k, v in d.items():
        if k == tkey or not isinstance(v, list):
            continue
        # sibling numeric array of the same length
        if len(v) == len(tl) and frac(sample(v), is_num_or_null) >= 0.9:
            sr = make_series(" / ".join(p for p in (hint, k) if p), zip(tl, v))
            if sr:
                out.append(sr)
        # list of series objects whose data arrays line up with the time array
        elif v and frac(sample(v), lambda e: isinstance(e, dict)) >= 0.9:
            for i, sobj in enumerate(v):
                if not isinstance(sobj, dict):
                    continue
                arr = next((sobj[f] for f in ("data", "values", "y", "points") if isinstance(sobj.get(f), list)), None)
                if arr is None or len(arr) != len(tl) or frac(sample(arr), is_num_or_null) < 0.9:
                    continue
                nm = name_hint(sobj, None) or f"{k}[{i}]"
                sr = make_series(" / ".join(p for p in (hint, nm) if p), zip(tl, arr))
                if sr:
                    out.append(sr)
    return out or None


def from_grafana_frame(d, hint):
    """Grafana data frame: {"schema": {"fields": [...]}, "data": {"values": [[...], ...]}}."""
    sch, data = d.get("schema"), d.get("data")
    if not (isinstance(sch, dict) and isinstance(data, dict)):
        return None
    fields, values = sch.get("fields"), data.get("values")
    if not (isinstance(fields, list) and isinstance(values, list) and len(fields) == len(values)):
        return None
    ti = next((i for i, f in enumerate(fields) if isinstance(f, dict)
               and (f.get("type") == "time" or str(f.get("name", "")).lower() in TIME_SET)), None)
    if ti is None or not isinstance(values[ti], list):
        return None
    times = values[ti]
    frame_name = sch.get("name") or hint or ""
    out = []
    for i, f in enumerate(fields):
        if i == ti or not isinstance(f, dict) or not isinstance(values[i], list):
            continue
        if f.get("type") not in (None, "number"):
            continue
        cfg = f.get("config") or {}
        nm = cfg.get("displayNameFromDS") or cfg.get("displayName") or f.get("name") or f"field{i}"
        if f.get("labels"):
            nm += "{" + labels_str(f["labels"]) + "}"
        if frame_name and frame_name not in nm:
            nm = f"{frame_name} / {nm}"
        sr = make_series(nm, zip(times, values[i]))
        if sr:
            out.append(sr)
    return out or None


def find_series(obj, path="$", hint=None, out=None, depth=0):
    """Walk a JSON value and collect (json_path, series) for every time series found."""
    if out is None:
        out = []
    if depth > 60:
        return out
    if isinstance(obj, dict):
        h = name_hint(obj, hint)
        got = from_grafana_frame(obj, h) or from_parallel(obj, h)
        if got:
            out.extend((path, s) for s in got)
            return out
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                find_series(v, f"{path}.{k}", h, out, depth + 1)
    elif isinstance(obj, list):
        got = from_pairs(obj, hint) or from_records(obj, hint)
        if got:
            out.extend((path, s) for s in got)
            return out
        for i, v in enumerate(obj):
            if isinstance(v, (dict, list)):
                find_series(v, f"{path}[{i}]", hint, out, depth + 1)
    return out


def parse_json_text(text):
    text = text.lstrip("\ufeff").strip()
    # strip anti-XSSI prefixes like )]}' or while(1);
    m = re.search(r"[\[{]", text[:20])
    if m and m.start() > 0:
        text = text[m.start():]
    return json.loads(text)

CHART_JS = r"""
() => {
  const out = [];
  const tryit = f => { try { f(); } catch (e) {} };
  const arr = a => Array.isArray(a) ? a : (a && typeof a.length === 'number' && typeof a !== 'string' ? Array.from(a) : []);
  const fx = v => (v instanceof Date) ? v.toISOString()
                 : (v === undefined || v === null) ? null
                 : (typeof v === 'number' || typeof v === 'string') ? v : String(v);
  const fy = v => (typeof v === 'number' || typeof v === 'string') ? v
                 : (v === null || v === undefined) ? null
                 : Array.isArray(v) ? fy(v[v.length - 1])
                 : (typeof v === 'object' && 'y' in v) ? fy(v.y) : null;
  const norm = (data, labels) => {
    const x = [], y = [];
    arr(data).forEach((d, i) => {
      const lab = labels && labels[i] !== undefined ? labels[i] : i;
      if (d === null || d === undefined || typeof d === 'number' || typeof d === 'string') { x.push(fx(lab)); y.push(fy(d)); }
      else if (Array.isArray(d)) { x.push(fx(d[0])); y.push(fy(d[d.length - 1])); }
      else if (typeof d === 'object') {
        const v = d.value;
        if (Array.isArray(v)) { x.push(fx(v[0])); y.push(fy(v[v.length - 1])); }
        else {
          const xv = d.x ?? d.t ?? d.time ?? d.date ?? d.name ?? lab;
          x.push(fx(xv)); y.push(fy(d.y ?? v ?? null));
        }
      }
    });
    return { x, y };
  };
  const push = (lib, chart, name, xy) => {
    if (xy && xy.x && xy.x.length) out.push({ lib, chart: String(chart), name: String(name ?? ''), x: xy.x, y: xy.y });
  };
  const markers = [];
  const mark = (lib, chart, axis, kind, from, to, label, dt) => {
    if (from === undefined && to === undefined) return;
    markers.push({ lib, chart: String(chart), axis: String(axis), kind,
                   from: fx(from ?? to), to: fx(to ?? from),
                   label: label == null ? '' : (Array.isArray(label) ? label.join(' ') : String(label)),
                   datetime: dt });
  };

  // Highcharts
  tryit(() => {
    const H = window.Highcharts;
    (H && H.charts || []).forEach((c, ci) => {
      if (!c || !c.series) return;
      const title = (c.title && c.title.textStr) || (c.renderTo && c.renderTo.id) || `highcharts_${ci}`;
      (c.axes || []).forEach(ax => tryit(() => {
        const axName = (ax.coll || (ax.isXAxis ? 'xAxis' : 'yAxis')) + (ax.index ? ax.index : '');
        const isDt = !!ax.dateTime || (ax.options && ax.options.type === 'datetime');
        const items = (ax.plotLinesAndBands && ax.plotLinesAndBands.length)
          ? ax.plotLinesAndBands.map(p => p.options || {})
          : [].concat((ax.options && ax.options.plotLines) || [], (ax.options && ax.options.plotBands) || []);
        items.forEach(o => {
          const label = o.label && o.label.text;
          if (o.value !== undefined) mark('highcharts', title, axName, 'line', o.value, o.value, label, isDt);
          else mark('highcharts', title, axName, 'band', o.from, o.to, label, isDt);
        });
      }));
      c.series.forEach(s => tryit(() => {
        if (s.options && s.options.isInternal) return;  // navigator series
        const cats = s.xAxis && s.xAxis.categories && s.xAxis.categories.length ? s.xAxis.categories : null;
        let xy = { x: [], y: [] };
        if (s.xData && s.xData.length) {
          xy.x = arr(s.xData).map(i => fx(cats && cats[i] !== undefined ? cats[i] : i));
          xy.y = arr(s.yData).map(fy);
        } else if (s.points && s.points.length) {
          s.points.forEach(p => { xy.x.push(fx(p.category ?? p.x)); xy.y.push(fy(p.y)); });
        } else if (s.options && s.options.data) {
          xy = norm(s.options.data, cats);
        }
        push('highcharts', title, s.name, xy);
      }));
    });
  });

  // Chart.js (v2-v4) -- only if the Chart global is exposed
  tryit(() => {
    const C = window.Chart;
    if (!C || !C.instances) return;
    Object.values(C.instances).forEach((c, ci) => tryit(() => {
      const cfg = (c.config && (c.config._config || c.config)) || {};
      const data = c.data || cfg.data || {};
      const opts = c.options || cfg.options || {};
      const title = (opts.plugins && opts.plugins.title && opts.plugins.title.text)
                 || (opts.title && opts.title.text)
                 || (c.canvas && (c.canvas.id || c.canvas.getAttribute('aria-label')))
                 || `chartjs_${ci}`;
      (data.datasets || []).forEach((ds, di) => push('chartjs', title, ds.label ?? `dataset_${di}`, norm(ds.data, data.labels)));
      const ann = opts.plugins && opts.plugins.annotation && opts.plugins.annotation.annotations;
      if (ann) Object.values(ann).forEach(a => tryit(() => {
        const label = a.label && (a.label.content ?? a.label.text);
        if (a.type === 'line') {
          const axis = a.scaleID || (a.yMin !== undefined ? 'y' : 'x');
          const v = a.value ?? a.yMin ?? a.xMin;
          mark('chartjs', title, axis, 'line', v, a.endValue ?? v, label, null);
        } else if (a.type === 'box') {
          if (a.xMin !== undefined || a.xMax !== undefined) mark('chartjs', title, 'x', 'band', a.xMin, a.xMax, label, null);
          else mark('chartjs', title, 'y', 'band', a.yMin, a.yMax, label, null);
        }
      }));
    }));
  });

  // Plotly
  tryit(() => {
    document.querySelectorAll('.js-plotly-plot').forEach((gd, gi) => tryit(() => {
      const lt = gd.layout && gd.layout.title;
      const title = (lt && (lt.text || (typeof lt === 'string' ? lt : ''))) || gd.id || `plotly_${gi}`;
      (gd.data || []).forEach((tr, ti) => {
        const xs = arr(tr.x), ys = arr(tr.y);
        if (!ys.length) return;
        push('plotly', title, tr.name ?? `trace_${ti}`, { x: (xs.length ? xs : ys.map((_, i) => i)).map(fx), y: ys.map(fy) });
      });
    }));
  });

  // ECharts
  tryit(() => {
    const E = window.echarts;
    if (!E) return;
    document.querySelectorAll('[_echarts_instance_]').forEach((el, ei) => tryit(() => {
      const inst = E.getInstanceByDom(el);
      if (!inst) return;
      const o = inst.getOption();
      const title = (o.title && o.title[0] && o.title[0].text) || el.id || `echarts_${ei}`;
      const xAxes = o.xAxis || [];
      (o.series || []).forEach((s, si) => {
        const ax = xAxes[s.xAxisIndex || 0];
        const labels = ax && ax.data ? ax.data.map(d => (d && typeof d === 'object') ? d.value : d) : null;
        push('echarts', title, s.name ?? `series_${si}`, norm(s.data, labels));
      });
    }));
  });

  // ApexCharts
  tryit(() => {
    const A = window.Apex;
    (A && A._chartInstances || []).forEach((ci, i) => tryit(() => {
      const w = ci.chart && ci.chart.w;
      if (!w) return;
      const g = w.globals;
      const title = (w.config.title && w.config.title.text) || ci.id || `apex_${i}`;
      (g.series || []).forEach((ys, si) => {
        if (!Array.isArray(ys)) return;
        const xs = (g.seriesX && g.seriesX[si] && g.seriesX[si].length) ? g.seriesX[si]
                 : (g.categoryLabels && g.categoryLabels.length ? g.categoryLabels : g.labels);
        push('apexcharts', title, (g.seriesNames || [])[si] ?? `series_${si}`, { x: arr(xs).map(fx), y: arr(ys).map(fy) });
      });
      const an = w.config.annotations || {};
      const xDt = !!(w.config.xaxis && w.config.xaxis.type === 'datetime');
      (an.yaxis || []).forEach(a => mark('apexcharts', title, 'y', a.y2 != null ? 'band' : 'line', a.y, a.y2 ?? a.y, a.label && a.label.text, false));
      (an.xaxis || []).forEach(a => mark('apexcharts', title, 'x', a.x2 != null ? 'band' : 'line', a.x, a.x2 ?? a.x, a.label && a.label.text, xDt));
    }));
  });

  return { series: out, markers };
}
"""

SCROLL_JS = r"""
() => {
  window.scrollBy(0, window.innerHeight);
  document.querySelectorAll('*').forEach(el => {
    if (el.scrollHeight > el.clientHeight + 50) {
      const st = getComputedStyle(el).overflowY;
      if (st === 'auto' || st === 'scroll') el.scrollTop += el.clientHeight;
    }
  });
}
"""
