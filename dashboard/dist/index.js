(function () {
  "use strict";
  // Cameras tab: last still, watch status, recent alerts, and the privacy limits. Read-only.
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useRef = SDK.hooks.useRef;
  var Card = SDK.components.Card;
  var CardHeader = SDK.components.CardHeader;
  var CardTitle = SDK.components.CardTitle;
  var CardContent = SDK.components.CardContent;
  var Badge = SDK.components.Badge;
  var API = "/api/plugins/hermes-camera-watch/state";
  var FALLBACK = "Stills stay on this machine. These limits cover this plugin's camera tools only, not a terminal or a config edit. A still is sent to the configured Hermes model only if you ask the agent to describe the saved file.";

  function kv(rows) {
    return h("dl", { className: "hcw-kv" }, rows.reduce(function (acc, row) {
      acc.push(h("dt", { key: row[0] + "k" }, row[0]));
      acc.push(h("dd", { key: row[0] + "v" }, row[1]));
      return acc;
    }, []));
  }

  function CamerasPage() {
    var stState = useState(null);
    var st = stState[0];
    var setSt = stState[1];
    var errState = useState(null);
    var err = errState[0];
    var setErr = errState[1];
    var imgState = useState(null);
    var img = imgState[0];
    var setImg = imgState[1];
    var since = useRef(0);

    useEffect(function () {
      var alive = true;
      function tick() {
        SDK.fetchJSON(API + "?since=" + since.current).then(function (d) {
          if (!alive) return;
          setSt(d);
          setErr(null);
          if (d.snapshot && d.snapshot.data_url) {
            since.current = d.snapshot.mtime;
            setImg(d.snapshot.data_url);
          }
        }).catch(function (e) {
          if (alive) setErr(String(e));
        });
      }
      tick();
      var timer = setInterval(tick, 2000);
      return function () { alive = false; clearInterval(timer); };
    }, []);

    var fallbackBanner = h("div", { className: "hcw-banner info" }, FALLBACK);
    if (err && !st) {
      return h("div", { className: "space-y-4" }, fallbackBanner,
        h(Card, null, h(CardContent, { className: "py-6" }, "Could not reach the camera-watch backend: " + err)));
    }
    if (!st) {
      return h("div", { className: "space-y-4" }, fallbackBanner,
        h(Card, null, h(CardContent, { className: "py-6 hcw-muted" }, "Loading…")));
    }

    var mock = st.backend !== "live";
    var unencrypted = (st.cameras || []).some(function (c) { return c.encrypted === false; });
    var bannerClass = unencrypted ? "warn" : (mock ? "info" : "ok");
    var bannerText = mock
      ? "Mock mode — scripted scenes only. No camera is contacted."
      : (unencrypted
        ? "Live — at least one camera uses unencrypted http or rtsp. https and rtsps are preferred."
        : "Live — only the cameras listed in this plugin's settings are contacted.");
    var disclosures = st.disclosures && st.disclosures.length ? st.disclosures : [FALLBACK];
    var events = st.events || [];
    var cameras = st.cameras || [];

    return h("div", { className: "space-y-4" },
      h("div", { className: "hcw-banner " + bannerClass }, bannerText),
      h("div", { className: "hcw-grid" },
        h(Card, null,
          h(CardHeader, null, h(CardTitle, null, "Latest still")),
          h(CardContent, null,
            img ? h("img", { className: "hcw-cam", src: img, alt: "latest saved camera still" })
                : h("div", { className: "hcw-cam hcw-muted", style: { padding: "4rem 1rem", textAlign: "center" } },
                    "No still yet. Ask for a snapshot or start a watch."),
            st.snapshot ? h("p", { className: "hcw-muted", style: { marginTop: "0.5rem", fontSize: "0.75rem" } },
              st.snapshot.name + " · " + st.snapshot.age_s + "s ago") : null)),
        h(Card, null,
          h(CardHeader, null, h(CardTitle, null, "Watches")),
          h(CardContent, null,
            cameras.length === 0
              ? h("span", { className: "hcw-muted" }, "No allowed cameras.")
              : h("div", { className: "hcw-log" }, cameras.map(function (c) {
                  return h("div", { key: c.name },
                    h("span", { className: "hcw-name" }, c.name),
                    h("span", { className: "hcw-muted" }, "  " + (c.mode || "") + (c.scene ? " · " + c.scene : "")),
                    h("div", null, c.watching ? "watching" : "idle",
                      c.last_caption ? " · " + c.last_caption : "",
                      c.last_score != null ? " · score " + c.last_score : ""));
                }))))),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Recent alerts ", h(Badge, { variant: "outline" }, String(events.length)))),
        h(CardContent, null,
          events.length === 0
            ? h("span", { className: "hcw-muted" }, "No scene changes yet. The first look at a camera only records a baseline.")
            : h("div", { className: "hcw-log" }, events.map(function (ev, i) {
                return h("div", { key: ev.id || i },
                  h("span", { className: "hcw-name" }, ev.camera),
                  h("span", { className: "hcw-muted" }, "  " + (ev.iso || "") + "  " + (ev.caption_source || "")),
                  h("div", null, ev.caption || ""));
              })))),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Privacy and safety")),
        h(CardContent, null,
          h("ul", { className: "hcw-list" }, disclosures.map(function (line, i) {
            return h("li", { key: i }, line);
          })),
          (st.warnings || []).length
            ? h("div", { className: "hcw-muted", style: { marginTop: "0.6rem", fontSize: "0.8rem" } }, st.warnings.join(" "))
            : null)));
  }

  window.__HERMES_PLUGINS__.register("hermes-camera-watch", CamerasPage);
})();
