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
            </div>
            {accounts.map((a) => (
              <article className="mail-account" key={a.id}>
                <div>
                  <h3>{a.body.name}</h3>
                  <p>
                    {a.body.address} ·{" "}
                    {a.body.enabled ? "收取已启用" : "收取已暂停"} ·{" "}
                    {a.body.credential_configured ? "凭证已保存" : "待配置凭证"}
                  </p>
                  <small>
                    {a.body.last_job?.kind} {a.body.last_job?.status}{" "}
                    {a.body.last_job?.error}
                  </small>
                </div>
                <div className="actions">
                  <button
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
                  </button>
                  <button
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
                  </button>
                  <button
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
                  </button>
                  <button
                    onClick={() =>
                      void act(
                        async () =>
                          waitJob(
                            (await api(`mail/accounts/${a.id}/sync`, {}))
                              .job_id,
                          ),
                        "同步任务已完成",
                      )
                    }
                  >
                    同步一轮
                  </button>
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
                </div>
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
                <p>新账号默认暂停。凭证只写不读，编辑时留空保留原凭证。</p>
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
