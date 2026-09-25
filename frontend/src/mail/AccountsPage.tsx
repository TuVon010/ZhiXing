import { useWorkspace } from "./context";
import { label, blankAccount } from "./shared";
export function AccountsPage() {
  const {
    page,
    accounts,
    account,
    setAccount,
    error,
    busy,
    accountForm,
    setAccountForm,
    setSelected,
    loadAccounts,
    navigate,
    act,
    waitJob,
    api,
  } = useWorkspace();
  return (
    <>
      {page === "accounts" && (
        <>
          <section className="panel">
            <div className="mail-sectionbar">
              <h3>已连接邮箱</h3>
              <button
                onClick={() => setAccountForm({ body: { ...blankAccount } })}
              >
                添加邮箱
              </button>
              <button disabled={busy} onClick={() => void act(async () => {
                const seeded = await api('mail/test-scenarios', {});
                await loadAccounts();
                setAccount(seeded.account_id);
                setSelected([seeded.account_id]);
                navigate('inbox');
              }, '已准备 14 封本地合成邮件，可在“收取与导入”分批分析')}>创建 Agent 测试邮箱（14 封）</button>
            </div>
            {accounts.map((a) => (
              <article className="mail-account" key={a.id}>
                <div>
                  <h3>{a.body.name}</h3>
                  <p>
                    {a.body.address} ·{" "}
                    {a.body.test_account ? '本地合成测试账号 · 无真实收发' : a.body.enabled ? "收取已启用" : "收取已暂停"} ·{" "}
                    {!a.body.test_account && (a.body.credential_configured ? "凭证已保存" : "待配置凭证")}
                  </p>
                  <small>
                    {a.body.last_job?.kind} {a.body.last_job?.status}{" "}
                    {a.body.last_job?.error}
                  </small>
                </div>
                <div className="actions">
                  {!a.body.test_account && <button
                    onClick={() =>
                      setAccountForm({
                        id: a.id,
                        body: {
                          ...blankAccount,
                          ...Object.fromEntries(
                            Object.entries(a.body).filter(
                              ([k]) => k in blankAccount,
                            ),
                          ),
                          password: "",
                        },
                      })
                    }
                  >
                    编辑
                  </button>}
                  {!a.body.test_account && <button
                    disabled={busy}
                    onClick={() =>
                      void act(
                        async () =>
                          waitJob(
                            (await api(`mail/accounts/${a.id}/test`, {}))
                              .job_id,
                          ),
                        "IMAP 与 SMTP 连接测试成功；没有发送邮件",
                      )
                    }
                  >
                    测试连接
                  </button>}
                  {!a.body.test_account && <button
                    onClick={() =>
                      void act(async () => {
                        await api(`mail/accounts/${a.id}/enabled`, {
                          enabled: !a.body.enabled,
                        });
                        await loadAccounts();
                      })
                    }
                  >
                    {a.body.enabled ? "暂停收取" : "启用收取"}
                  </button>}
                  {!a.body.test_account && <button
                    onClick={() =>
                      void act(
                        async () =>
                          waitJob(
                            (await api(`mail/accounts/${a.id}/sync`, {}))
                              .job_id,
                          ),
                        a.body.auto_import_enabled ? "新邮件同步完成；最近邮件自动补齐已排队" : "同步任务已完成",
                      )
                    }
                  >
                    同步一轮
                  </button>}
                </div>
              </article>
            ))}
          </section>
          {accountForm && (
            <section className="panel">
              <h3>{accountForm.id ? "编辑邮箱" : "添加邮箱"}</h3>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    const b = {
                      ...accountForm.body,
                      password: accountForm.body.password || null,
                    };
                    const r = await api(
                      "mail/accounts" +
                        (accountForm.id ? "/" + accountForm.id : ""),
                      b,
                    );
                    await loadAccounts();
                    setAccount(r.id);
                    setSelected([r.id]);
                    setAccountForm(null);
                  }, "邮箱已保存");
                }}
              >
                <div className="pricing-grid">
                  {[
                    ["name", "邮箱名称"],
                    ["address", "邮箱地址"],
                    ["username", "登录名"],
                    ["password", "授权码 / 密码"],
                  ].map(([k, n]) => (
                    <label key={k}>
                      {n}
                      <input
                        aria-label={n}
                        type={k === "password" ? "password" : "text"}
                        autoComplete="off"
                        required={["name", "address"].includes(k)}
                        value={accountForm.body[k]}
                        onChange={(e) =>
                          setAccountForm({
                            ...accountForm,
                            body: { ...accountForm.body, [k]: e.target.value },
                          })
                        }
                      />
                    </label>
                  ))}
                  <label>
                    服务商
                    <select
                      aria-label="服务商"
                      value={accountForm.body.provider}
                      onChange={(e) =>
                        setAccountForm({
                          ...accountForm,
                          body: {
                            ...accountForm.body,
                            provider: e.target.value,
                          },
                        })
                      }
                    >
                      <option value="qq">QQ</option>
                      <option value="163">163</option>
                      <option value="custom">通用 IMAP / SMTP</option>
                    </select>
                  </label>
                  {["imap", "smtp"].map((kind) => (
                    <div key={kind}>
                      <label>
                        {kind.toUpperCase()} 主机
                        <input
                          aria-label={kind + "主机"}
                          disabled={accountForm.body.provider !== "custom"}
                          value={accountForm.body[kind + "_host"]}
                          onChange={(e) =>
                            setAccountForm({
                              ...accountForm,
                              body: {
                                ...accountForm.body,
                                [kind + "_host"]: e.target.value,
                              },
                            })
                          }
                        />
                      </label>
                      <label>
                        端口
                        <input
                          type="number"
                          value={accountForm.body[kind + "_port"]}
                          onChange={(e) =>
                            setAccountForm({
                              ...accountForm,
                              body: {
                                ...accountForm.body,
                                [kind + "_port"]: +e.target.value,
                              },
                            })
                          }
                        />
                      </label>
                      <select
                        aria-label={kind + " TLS"}
                        value={accountForm.body[kind + "_tls"]}
                        onChange={(e) =>
                          setAccountForm({
                            ...accountForm,
                            body: {
                              ...accountForm.body,
                              [kind + "_tls"]: e.target.value,
                            },
                          })
                        }
                      >
                        <option value="ssl">隐式 TLS</option>
                        <option value="starttls">STARTTLS</option>
                      </select>
                    </div>
                  ))}
                  <label>
                    每轮扫描上限
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={accountForm.body.scan_limit}
                      onChange={(e) =>
                        setAccountForm({
                          ...accountForm,
                          body: {
                            ...accountForm.body,
                            scan_limit: +e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    自动分析每小时上限
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={accountForm.body.hourly_analysis_limit}
                      onChange={(e) =>
                        setAccountForm({
                          ...accountForm,
                          body: {
                            ...accountForm.body,
                            hourly_analysis_limit: +e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    自动补齐最近天数
                    <input
                      type="number"
                      min={1}
                      max={30}
                      disabled={!accountForm.body.auto_import_enabled}
                      value={accountForm.body.auto_import_days}
                      onChange={(e) =>
                        setAccountForm({
                          ...accountForm,
                          body: {
                            ...accountForm.body,
                            auto_import_days: +e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                  <label>
                    新邮件检查间隔（秒）
                    <input type="number" min={10} max={300} value={accountForm.body.poll_interval_seconds}
                      onChange={(e) => setAccountForm({ ...accountForm, body: { ...accountForm.body, poll_interval_seconds: +e.target.value } })} />
                  </label>
                </div>
                <label>
                  <input
                    type="checkbox"
                    checked={accountForm.body.auto_import_enabled}
                    onChange={(e) =>
                      setAccountForm({
                        ...accountForm,
                        body: {
                          ...accountForm.body,
                          auto_import_enabled: e.target.checked,
                        },
                      })
                    }
                  />
                  自动补齐最近邮件（默认 7 天，每日检查一次）
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={accountForm.body.auto_analyze}
                    onChange={(e) =>
                      setAccountForm({
                        ...accountForm,
                        body: {
                          ...accountForm.body,
                          auto_analyze: e.target.checked,
                        },
                      })
                    }
                  />
                  自动分析已放行的新邮件（可能调用外部模型）
                </label>
                <p>自动补齐与模型分析分别控制；重复范围只记录新增邮件。新账号默认暂停，凭证只写不读，编辑时留空保留原凭证。</p>
                <button className="primary" disabled={busy}>
                  保存邮箱
                </button>
                <button type="button" onClick={() => setAccountForm(null)}>
                  取消
                </button>
              </form>
            </section>
          )}
        </>
      )}
    </>
  );
}
