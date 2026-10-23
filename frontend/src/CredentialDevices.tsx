import { useEffect, useState } from "react";
import { Api, ApiError, type Account } from "./api";
import { t, locale } from "./i18n";
import { Modal } from "./ui";
import { parsePairingPackage } from "./pairing-package";

type Device = {
  id: string;
  expires_at: number | string;
  revoked: boolean;
  label?: string;
  new_account_limit?: number;
  new_account_quota?: number;
};
type DeviceResponse = Omit<Device, "id"> & { device_id?: string; id?: string };
type Challenge = {
  challenge: string;
  server_id: string;
  fingerprint: string;
  server_origin: string;
  expires_at: number | string;
};
const disabledMessage =
  "服务未开启凭据导入（404），需管理员开启。账号池其他功能不受影响。";
function expires(value: number | string) {
  return typeof value === "number" ? value * 1000 : Date.parse(value);
}

function time(value: number | string) {
  return new Date(
    typeof value === "number" ? value * 1000 : value,
  ).toLocaleString(locale(), { hour12: false });
}

/** 独立管理入口：挑战只留在当前弹窗内存，不接触上游会话内容。 */
export function CredentialDevices({
  api,
  accounts,
  onClose,
}: {
  api: Api;
  accounts: Account[];
  onClose: () => void;
}) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [challenge, setChallenge] = useState<Challenge>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [revision, setRevision] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [pairingLabel, setPairingLabel] = useState("");
  const [newAccountLimit, setNewAccountLimit] = useState(1);
  const [deviceId, setDeviceId] = useState("");
  const [captureId, setCaptureId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [pending, setPending] = useState<
    | { kind: "revoke"; id: string }
    | {
        kind: "identity";
        device_id: string;
        capture_id: string;
        account_id: string;
      }
  >();
  useEffect(() => {
    setChallenge(undefined);
    const controller = new AbortController();
    let stopped = false;
    setLoading(true);
    setError("");
    void api
      .request<DeviceResponse[] | { data: DeviceResponse[] }>(
        "/credential-devices",
        "GET",
        undefined,
        controller.signal,
      )
      .then((result) => {
        const rows = Array.isArray(result) ? result : result.data;
        const normalized = rows.map((row) => {
          const id = row.id ?? row.device_id;
          if (typeof id !== "string" || !id) throw new Error();
          return {
            id,
            expires_at: row.expires_at,
            revoked: row.revoked,
            label: row.label,
            new_account_limit: row.new_account_limit ?? row.new_account_quota,
          };
        });
        if (!stopped) setDevices(normalized);
      })
      .catch((e: unknown) => {
        if (!stopped) {
          setDevices([]);
          setError(
            e instanceof ApiError && e.status === 404
              ? disabledMessage
              : "设备列表读取失败，请重试。",
          );
        }
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [api, revision]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const pairingPackage =
    challenge && expires(challenge.expires_at) > now
      ? JSON.stringify(
          {
            schema: "muse-console-pairing",
            version: 1,
            origin: challenge.server_origin,
            server_id: challenge.server_id,
            fingerprint: challenge.fingerprint,
            challenge: challenge.challenge,
            expires_at: expires(challenge.expires_at) / 1000,
          },
          null,
          2,
        )
      : "";
  const activeDevices = devices.filter(
    (d) => !d.revoked && expires(d.expires_at) > now,
  );
  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
    } catch (e) {
      // 不把服务端任意错误载荷（可能包含秘密）渲染进 DOM。
      setError(
        e instanceof ApiError && e.status === 404
          ? disabledMessage
          : "操作失败，请检查设备、候选和账号后重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={t("扩展设备配对")}
      wide
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="muted">
        {t(
          "扩展会把浏览器里的 Muse 登录交给本服务器。授权前核对下面三项与扩展一致；扩展拿不到管理员 Key。",
        )}
      </p>
      <dl>
        <dt>{t("服务地址")}</dt>
        <dd className="mono">
          {challenge?.server_origin ?? t("生成配对挑战后显示")}
        </dd>
        <dt>{t("服务器 ID")}</dt>
        <dd className="mono">
          {challenge?.server_id ?? t("生成配对挑战后显示")}
        </dd>
        <dt>{t("应用指纹（非 TLS 证书指纹）")}</dt>
        <dd className="mono">
          {challenge?.fingerprint ?? t("生成配对挑战后显示")}
        </dd>
      </dl>
      <p className="fine">{t("指纹不能代替 HTTPS 证书校验。")}</p>
      {error && (
        <p role="alert" className="error">
          {t(error)}
        </p>
      )}
      {message && <p role="status">{t(message)}</p>}
      <h3>{t("一次性配对")}</h3>
      <p className="fine">
        {t(
          "配对码短时有效、只能用一次，关窗即隐藏。额度＝该设备最多自动新增的账号数。",
        )}
      </p>
      <p className="fine">
        {t(
          "配对包短期敏感，勿外传。预填不是信任来源，仍须核对可信控制台身份。",
        )}
      </p>
      <div className="pairing-controls">
        <label>
          {t("配对设备备注（可选）")}
          <input
            maxLength={100}
            autoComplete="off"
            value={pairingLabel}
            disabled={busy || loading}
            placeholder={t("例如：办公主力浏览器")}
            onChange={(e) => setPairingLabel(e.target.value)}
          />
        </label>
        <label>
          {t("自动新增账号额度（0–10）")}
          <input
            type="number"
            min={0}
            max={10}
            value={newAccountLimit}
            disabled={busy || loading}
            onChange={(e) => {
              const parsed = Number(e.target.value);
              setNewAccountLimit(
                Number.isNaN(parsed)
                  ? 0
                  : Math.max(0, Math.min(10, Math.trunc(parsed))),
              );
            }}
          />
        </label>
      </div>
      <button
        disabled={busy || loading}
        onClick={() =>
          void run(async () => {
            setChallenge(undefined);
            const result = await api.request<Challenge>(
              "/credential-devices/pairings",
              "POST",
              {
                label: pairingLabel.trim(),
                new_account_limit: Math.max(
                  0,
                  Math.min(10, Number(newAccountLimit) || 0),
                ),
              },
            );
            setNow(Date.now());
            if (expires(result.expires_at) > Date.now()) {
              parsePairingPackage(
                JSON.stringify({
                  schema: "muse-console-pairing",
                  version: 1,
                  origin: result.server_origin,
                  server_id: result.server_id,
                  fingerprint: result.fingerprint,
                  challenge: result.challenge,
                  expires_at: expires(result.expires_at) / 1000,
                }),
                true,
              );
            }
            setChallenge(result);
          })
        }
      >
        {t("生成配对挑战")}
      </button>
      {challenge &&
        (expires(challenge.expires_at) > now ? (
          <>
            <label>
              {t("快速配对包（含一次性挑战，勿外传）")}
              <textarea
                readOnly
                autoComplete="off"
                spellCheck={false}
                value={pairingPackage}
                onFocus={(e) => e.currentTarget.select()}
              />
            </label>
            <button
              disabled={busy}
              onClick={async () => {
                if (expires(challenge.expires_at) <= Date.now()) return;
                try {
                  await navigator.clipboard.writeText(pairingPackage);
                  setMessage(
                    "配对包已复制。请在扩展粘贴，仍须核对身份并单独同意同步。",
                  );
                } catch {
                  setMessage("无法访问剪贴板，请选中上方配对包手动复制。");
                }
              }}
            >
              {t("复制快速配对包")}
            </button>
            <p className="fine">
              {t(
                "仅在扩展手动粘贴；不会自动授权或开启同步。本页面不保存配对包，复制后请及时清理剪贴板。",
              )}
            </p>
            <label>
              {t("一次性配对挑战")}
              <input
                readOnly
                autoComplete="off"
                spellCheck={false}
                value={challenge.challenge}
                onFocus={(e) => e.currentTarget.select()}
              />
            </label>
            <p className="fine">
              {t("剩余 {seconds}s（{expiry} 到期）。手动填入扩展，勿外传。", {
                seconds: Math.max(
                  0,
                  Math.ceil((expires(challenge.expires_at) - now) / 1000),
                ),
                expiry: time(challenge.expires_at),
              })}
            </p>
          </>
        ) : (
          <p role="status">{t("配对挑战已过期，请重新生成。")}</p>
        ))}
      <h3>{t("已配对设备")}</h3>
      <button
        disabled={busy || loading}
        onClick={() => {
          setPending(undefined);
          setRevision((v) => v + 1);
        }}
      >
        {t("刷新设备列表")}
      </button>
      {loading ? (
        <p role="status">{t("正在读取设备…")}</p>
      ) : !error && devices.length === 0 ? (
        <p className="muted">{t("暂无已配对设备。")}</p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("设备 ID")}</th>
              <th>{t("备注")}</th>
              <th>{t("到期时间")}</th>
              <th>{t("状态")}</th>
              <th>{t("新增账号额度")}</th>
              <th>{t("操作")}</th>
            </tr>
          </thead>
          <tbody>
            {devices.map((device) => (
              <tr key={device.id}>
                <td className="mono">{device.id}</td>
                <td>{device.label || "—"}</td>
                <td>{time(device.expires_at)}</td>
                <td>
                  {device.revoked
                    ? t("已撤销")
                    : expires(device.expires_at) <= now
                      ? t("已过期")
                      : t("有效")}
                </td>
                <td>{device.new_account_limit ?? t("未提供")}</td>
                <td>
                  <button
                    disabled={busy || device.revoked}
                    onClick={() =>
                      setPending({ kind: "revoke", id: device.id })
                    }
                  >
                    {t("撤销设备 {id}", { id: device.id })}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>{t("身份冲突确认")}</h3>
      <p className="fine">
        {t(
          "扩展提示冲突时使用：填它给的 capture_id，选设备和目标账号。只对这一次生效。",
        )}
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (
            activeDevices.some((d) => d.id === deviceId) &&
            accounts.some((a) => a.id === accountId) &&
            captureId.trim()
          )
            setPending({
              kind: "identity",
              device_id: deviceId,
              capture_id: captureId.trim(),
              account_id: accountId,
            });
        }}
      >
        <label>
          {t("设备 device_id")}
          <select
            required
            value={deviceId}
            disabled={busy}
            onChange={(e) => {
              setDeviceId(e.target.value);
              setPending(undefined);
            }}
          >
            <option value="">{t("选择有效设备")}</option>
            {activeDevices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label ? `${d.id} (${d.label})` : d.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("候选 capture_id")}
          <input
            required
            maxLength={200}
            autoComplete="off"
            value={captureId}
            disabled={busy}
            onChange={(e) => {
              setCaptureId(e.target.value);
              setPending(undefined);
            }}
          />
        </label>
        <label>
          {t("目标 account_id")}
          <select
            required
            value={accountId}
            disabled={busy}
            onChange={(e) => {
              setAccountId(e.target.value);
              setPending(undefined);
            }}
          >
            <option value="">{t("选择现有账号")}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label ? `${a.id} (${a.label})` : a.id}
                {a.credential_version ? ` · v${a.credential_version}` : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={
            busy ||
            loading ||
            !captureId.trim() ||
            !activeDevices.some((d) => d.id === deviceId) ||
            !accounts.some((a) => a.id === accountId)
          }
        >
          {t("核对本次候选")}
        </button>
      </form>
      {pending && (
        <section aria-label={t("操作确认")}>
          <h3>
            {pending.kind === "revoke"
              ? t("确认撤销设备？")
              : t("确认本次身份冲突？")}
          </h3>
          {pending.kind === "revoke" ? (
            <p>
              {t("设备 {id} 将不能继续提交凭据；不会删除已入池账号。", {
                id: pending.id,
              })}
            </p>
          ) : (
            <p className="mono">
              device_id: {pending.device_id}
              <br />
              capture_id: {pending.capture_id}
              <br />
              account_id: {pending.account_id}
              <br />
              {t(
                "请确认浏览器当前登录的就是该账号。只对本次候选生效，账号版本再变需重新确认。",
              )}
            </p>
          )}
          <button disabled={busy} onClick={() => setPending(undefined)}>
            {t("取消操作")}
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                if (pending.kind === "revoke") {
                  await api.request(
                    `/credential-devices/${encodeURIComponent(pending.id)}`,
                    "DELETE",
                  );
                  setDevices((rows) =>
                    rows.map((d) =>
                      d.id === pending.id ? { ...d, revoked: true } : d,
                    ),
                  );
                  setMessage("设备已撤销。");
                } else {
                  const { device_id, capture_id, account_id } = pending;
                  await api.request(
                    `/credential-devices/${encodeURIComponent(device_id)}/confirm`,
                    "POST",
                    { capture_id, account_id },
                  );
                  setCaptureId("");
                  setMessage(
                    "已确认，尚未入池。请在扩展重传同一候选，服务端验证后写入。",
                  );
                }
                setPending(undefined);
              })
            }
          >
            {pending.kind === "revoke" ? t("确认撤销") : t("仅确认本次候选")}
          </button>
        </section>
      )}
      <footer>
        <button disabled={busy} onClick={onClose}>
          {t("关闭配对窗口")}
        </button>
      </footer>
    </Modal>
  );
}
