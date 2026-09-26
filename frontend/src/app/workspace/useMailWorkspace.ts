import { useEffect, useState } from "react";
import type { Api, Row } from "../../shared/mail";
export function useMailWorkspace(api: Api) {
  const stored = (key: string, fallback: string) => localStorage.getItem("zhixing." + key) || fallback;
  const [page, setPage] = useState(() => {
      const saved=stored("page", "home");
      return saved==="approvals" ? "drafts" : saved;
    }),
    [accounts, setAccounts] = useState<Row[]>([]),
    [account, setAccount] = useState(() => stored("account", "")),
    [list, setList] = useState<Row[]>([]),
    [offset, setOffset] = useState(0),
    [config, setConfig] = useState<any>({}),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [opened, setOpened] = useState<any>(null),
    [thread, setThread] = useState<any>(null),
    [accountForm, setAccountForm] = useState<any>(null),
    [draftForm, setDraftForm] = useState<any>(null),
    [query, setQuery] = useState(""),
    [selected, setSelected] = useState<string[]>(() => { try { return JSON.parse(stored("agentAccounts", "[]")); } catch { return []; } }),
    [searchResult, setSearchResult] = useState<any>(null),
    [session, setSession] = useState(""),
    [turns, setTurns] = useState<Row[]>([]),
    [trace, setTrace] = useState<any>(null),
    [rules, setRules] = useState("");
  const [range, setRange] = useState({ start: "", end: "", limit: 100 }),
    [job, setJob] = useState<any>(null),
    [imports, setImports] = useState<Row[]>([]),
    [memories, setMemories] = useState<Row[]>([]),
    [memory, setMemory] = useState(""),
    [globalMemory, setGlobalMemory] = useState(false);
  const [ready, setReady] = useState(false),
    [sessions, setSessions] = useState<Row[]>([]);
  const [unread, setUnread] = useState(0);
  const [showTest, setShowTest] = useState(() => stored("showTest", "false") === "true"),
    [inboxSort, setInboxSort] = useState(() => stored("inboxSort", "smart")),
    [inboxView, setInboxView] = useState(() => stored("inboxView", "all")),
    [inboxCategory, setInboxCategory] = useState(() => stored("inboxCategory", "")),
    [home, setHome] = useState<any>(null);
  useEffect(() => localStorage.setItem("zhixing.page", page), [page]);
  useEffect(() => localStorage.setItem("zhixing.account", account), [account]);
  useEffect(() => localStorage.setItem("zhixing.agentAccounts", JSON.stringify(selected)), [selected]);
  useEffect(() => localStorage.setItem("zhixing.showTest", String(showTest)), [showTest]);
  useEffect(() => localStorage.setItem("zhixing.inboxSort", inboxSort), [inboxSort]);
  useEffect(() => localStorage.setItem("zhixing.inboxView", inboxView), [inboxView]);
  useEffect(() => localStorage.setItem("zhixing.inboxCategory", inboxCategory), [inboxCategory]);
  useEffect(() => {
    if (!ready) return;
    let active = true;
    const load = async () => {
      try {
        const value = await api(
          "mail/notifications?limit=1" +
            (account ? "&account_id=" + account : ""),
        );
        if (active) setUnread(value.unread);
      } catch {
        /* The notification page exposes retryable connection errors. */
      }
    };
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [ready, account, api]);
  useEffect(() => {
    if (!trace?.root) return;
    let active = true;
    const timer = setInterval(() => {
      void api("mail/traces/" + trace.id)
        .then((value) => {
          if (active) setTrace(value);
        })
        .catch((e) => {
          if (active) setError(String(e));
        });
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [trace?.id, api]);
  const current = accounts.find((a) => a.id === account);
  async function loadAccounts() {
    const data = await api("mail/accounts");
    setAccounts(data.items);
    return data.items as Row[];
  }
  async function refresh(target = page) {
    if (target === "home")
      setHome(await api(`mail/home?include_test=${showTest}${account ? "&account_id=" + encodeURIComponent(account) : ""}`));
    if (["inbox", "filter"].includes(target)) {
      const suffix = target === "filter" ? "&status=filtered" : "&status=inbox";
      setList(
        (
          await api(
            `mail/messages?limit=30&offset=${offset}${account ? "&account_id=" + account : ""}${suffix}&include_test=${showTest}&sort=${inboxSort}&view=${inboxView}&category=${encodeURIComponent(inboxCategory)}`,
          )
        ).items,
      );
    }
    if (target === "drafts")
      setList(
        (await api("mail/drafts" + (account ? "?account_id=" + account : "")))
          .items,
      );
    if (target === "assistant")
      setSessions((await api("assistant/sessions")).items);
    if (target === "accounts") await loadAccounts();
    if (target === "imports")
      setImports(
        (await api("mail/imports" + (account ? "?account_id=" + account : "")))
          .items,
      );
    if (target === "memory")
      setMemories(
        (await api("memory?limit=200")).items.filter(
          (r: Row) => (r.scope === "global" || r.scope === account) && r.status !== "trashed",
        ),
      );
    if (target === "followups")
      setList(
        (
          await api(
            "mail/followups" + (account ? "?account_id=" + account : ""),
          )
        ).items,
      );
  }
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        await api("session");
        const cfg = await api("settings");
        const rows = await loadAccounts();
        if (!cancelled) {
          setConfig(cfg);
          setReady(true);
          const validAccount=rows.some((r) => r.id===account) ? account : "";
          const preferred=validAccount || rows.find((r) => r.body.enabled && !r.body.test_account)?.id || rows.find((r) => !r.body.test_account)?.id || "";
          setAccount(preferred);
          const validSelected=selected.filter((id) => rows.some((r) => r.id===id));
          setSelected(validSelected.length ? validSelected : preferred ? [preferred] : []);
        }
      } catch (e) {
        setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (ready) void refresh().catch((e) => setError(String(e)));
  }, [ready, page, account, offset, showTest, inboxSort, inboxView, inboxCategory]);
  useEffect(() => {
    if (!ready) return;
    const source = new EventSource("/api/stream");
    source.onmessage = () => void refresh().catch((e) => setError(String(e)));
    return () => source.close();
  }, [ready, page, account, offset, showTest, inboxSort, inboxView, inboxCategory]);
  async function act(fn: () => Promise<any>, message = "已保存") {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await fn();
      setNotice(message);
      await refresh();
      return result;
    } catch (e) {
      setError(String(e));
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function waitJob(id: string) {
    for (let i = 0; i < 240; i++) {
      const j = await api("mail/jobs/" + id);
      setJob(j);
      if (["completed", "failed"].includes(j.status)) {
        if (j.status === "failed")
          throw new Error(j.result?.detail || j.result?.error);
        return j.result;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error("任务仍在后台运行，可在任务状态中继续查看");
  }
  async function openMail(id: string) {
    await act(async () => {
      await api("mail/messages/" + id + "/read", { read: true });
      const m = await api("mail/messages/" + id);
      setOpened(m);
      setThread(await api("mail/threads/" + m.body.thread_id));
    }, "");
  }
  function navigate(p: string) {
    setPage(p);
    setOffset(0);
    setOpened(null);
    setThread(null);
    setError("");
    setNotice("");
  }
  async function makeDraft(mode = "new") {
    await act(async () => {
      const a = opened?.body.account_id || account;
      if (!a) throw new Error("请先选择发件邮箱");
      const result = await api("mail/drafts", {
        account_id: a,
        mode,
        ...(mode !== "new" ? { message_id: opened.id } : {}),
      });
      setDraftForm(result);
      setPage("drafts");
      setOpened(null);
      await refresh("drafts");
    }, "已创建草稿");
  }
  function draftPayload() {
    const b = draftForm.body;
    return {
      account_id: b.account_id,
      message_id: b.message_id,
      mode: b.mode,
      to: b.to.filter(Boolean),
      cc: b.cc.filter(Boolean),
      subject: b.subject,
      content: b.content,
      version: b.version,
    };
  }
  function rangePayload() {
    if (!account || !range.start || !range.end)
      throw new Error("请先选择邮箱和起止时间");
    return {
      account_id: account,
      start: new Date(range.start).toISOString(),
      end: new Date(range.end).toISOString(),
      limit: range.limit,
    };
  }
  async function ask() {
    await act(async () => {
      if (!selected.length) throw new Error("请选择允许检索的邮箱");
      let sid = session;
      if (!sid) {
        const s = await api("assistant/sessions", {
          account_ids: selected,
          ...(opened ? { thread_id: opened.body.thread_id } : {}),
        });
        sid = s.id;
        setSession(sid);
      }
      const turn = await api(`assistant/sessions/${sid}/turns`, {
        text: query,
      });
      setQuery("");
      setTurns((t) => [turn, ...t]);
      for (let i = 0; i < 240; i++) {
        const detail = await api("assistant/turns/" + turn.id);
        setTurns((t) => t.map((r) => (r.id === turn.id ? detail.turn : r)));
        if (!["queued", "running"].includes(detail.turn.status)) {
          setTrace(await api("mail/traces/" + turn.id));
          return;
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      setNotice("Agent 仍在后台运行，请刷新会话");
    }, "本轮已记录");
  }

  return {
    unread,
    home,
    showTest,
    setShowTest,
    inboxSort,
    setInboxSort,
    inboxView,
    setInboxView,
    inboxCategory,
    setInboxCategory,
    page,
    setPage,
    accounts,
    setAccounts,
    account,
    setAccount,
    list,
    setList,
    offset,
    setOffset,
    config,
    setConfig,
    error,
    setError,
    notice,
    setNotice,
    busy,
    setBusy,
    opened,
    setOpened,
    thread,
    setThread,
    accountForm,
    setAccountForm,
    draftForm,
    setDraftForm,
    query,
    setQuery,
    selected,
    setSelected,
    searchResult,
    setSearchResult,
    session,
    setSession,
    turns,
    setTurns,
    trace,
    setTrace,
    rules,
    setRules,
    range,
    setRange,
    job,
    setJob,
    imports,
    setImports,
    memories,
    setMemories,
    memory,
    setMemory,
    globalMemory,
    setGlobalMemory,
    ready,
    setReady,
    sessions,
    setSessions,
    current,
    loadAccounts,
    refresh,
    act,
    waitJob,
    openMail,
    navigate,
    makeDraft,
    draftPayload,
    rangePayload,
    ask,
    api,
  };
}
