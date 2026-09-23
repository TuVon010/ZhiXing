import { useWorkspace } from "./context";
import { pretty, label } from "./shared";
import { PricingSettings } from "../PricingSettings";
export function SettingsPage() {
  const { page, accounts, account, setTrace, rules, setRules, act, api } =
    useWorkspace();
  return (
    <>
      {page === "settings" && (
        <>
          <PricingSettings api={api} />
          <section className="panel">
            <h3>当前邮箱过滤规则</h3>
            <p>
              编辑完整规则
              JSON；只影响当前邮箱后续收取，可在过滤箱纠正已收取邮件。
            </p>
            <button
              disabled={!account}
              onClick={() =>
                void act(async () =>
                  setRules(
                    pretty(await api(`mail/accounts/${account}/filters`)),
                  ),
                )
              }
            >
              读取过滤规则
            </button>
            {rules && (
              <>
                <textarea
                  aria-label="邮箱过滤规则"
                  rows={12}
                  value={rules}
                  onChange={(e) => setRules(e.target.value)}
                />
                <button
                  onClick={() =>
                    void act(async () => {
                      const value = JSON.parse(rules);
                      delete value.version;
                      setRules(
                        pretty(
                          await api(`mail/accounts/${account}/filters`, value),
                        ),
                      );
                    })
                  }
                >
                  保存过滤规则
                </button>
              </>
            )}
            <button
              disabled={!account}
              onClick={() =>
                void act(
                  () => api(`mail/accounts/${account}/reindex`, {}),
                  "已排队重建当前账号索引",
                )
              }
            >
              重建本地检索索引
            </button>
          </section>
          <section className="panel">
            <h3>升级与历史数据</h3>
            <button
              onClick={() =>
                void act(async () => setTrace(await api("mail/migration")))
              }
            >
              查看迁移复核
            </button>
            <p>
              模型密钥通过项目根目录的 .env
              配置；邮箱授权码在“邮箱账号”中保存并加密。旧任务不会在升级后自动执行。
            </p>
          </section>
        </>
      )}
    </>
  );
}
