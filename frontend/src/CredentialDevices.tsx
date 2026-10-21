import { useEffect, useState } from "react";
import { Api, ApiError, time, type Account } from "./api";
import { Modal } from "./ui";

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
  "设备凭据导入尚未启用或当前服务不支持此功能（404）。请管理员开启凭据导入配置后重试；账号池其他功能不受影响。";
function expires(value: number | string) {
  return typeof value === "number" ? value * 1000 : Date.parse(value);
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
          : "操作失败，请检查设备、候选与账号是否有效，然后重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="扩展设备配对"
      wide
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <p className="muted">
        扩展仅向固定服务器提交 Muse 会话候选。请核对精确服务地址、服务器 ID
        和应用指纹，再单独授权发送：这会将 Muse
        会话授权交给该服务器。扩展不接收管理员 Key。
      </p>
      <dl>
        <dt>服务地址（服务端配置 origin）</dt>
        <dd className="mono">
          {challenge?.server_origin ?? "生成配对挑战后显示"}
        </dd>
        <dt>服务器 ID</dt>
        <dd className="mono">{challenge?.server_id ?? "生成配对挑战后显示"}</dd>
        <dt>应用指纹 fingerprint（非 TLS 证书指纹）</dt>
        <dd className="mono">
          {challenge?.fingerprint ?? "生成配对挑战后显示"}
        </dd>
      </dl>
      <p className="fine">
        应用指纹不能替代 TLS 证书验证。生产环境必须使用可信
        HTTPS；仅本机开发可显式允许 loopback。
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      <h3>一次性配对</h3>
      <p className="fine">
        点击生成，短时有效，仅可兑换一次；到期时间见下方。设备有效期由服务端配置，允许自动新增
        1
        个账号，仍受服务端权限限制。挑战仅存于弹窗内存，关闭即隐藏，不自动复制。兑换后刷新设备列表。
      </p>
      <button
        disabled={busy || loading}
        onClick={() =>
          void run(async () => {
            setChallenge(undefined);
            const result = await api.request<Challenge>(
              "/credential-devices/pairings",
              "POST",
              { label: "", new_account_limit: 1 },
            );
            setNow(Date.now());
            setChallenge(result);
          })
        }
      >
        生成配对挑战
      </button>
      {challenge &&
        (expires(challenge.expires_at) > now ? (
          <>
            <label>
              一次性配对挑战
              <input
                readOnly
                autoComplete="off"
                spellCheck={false}
                value={challenge.challenge}
              />
            </label>
            <p className="fine">
              到期时间：{time(challenge.expires_at)}
              。请在已核对的扩展中手动输入；不要分享或记录此挑战。
            </p>
          </>
        ) : (
          <p role="status">配对挑战已过期，请重新生成。</p>
        ))}
      <h3>已配对设备</h3>
      <button
        disabled={busy || loading}
        onClick={() => {
          setPending(undefined);
          setRevision((v) => v + 1);
        }}
      >
        刷新设备列表
      </button>
      {loading ? (
        <p role="status">正在读取设备…</p>
      ) : !error && devices.length === 0 ? (
        <p className="muted">暂无已配对设备。</p>
      ) : null}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>设备 ID</th>
              <th>备注</th>
              <th>到期时间</th>
              <th>状态</th>
              <th>新增账号额度</th>
              <th>操作</th>
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
                    ? "已撤销"
                    : expires(device.expires_at) <= now
                      ? "已过期"
                      : "有效"}
                </td>
                <td>{device.new_account_limit ?? "未提供"}</td>
                <td>
                  <button
                    disabled={busy || device.revoked}
                    onClick={() =>
                      setPending({ kind: "revoke", id: device.id })
                    }
                  >
                    撤销设备 {device.id}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>本次候选身份或版本冲突确认</h3>
      <p className="fine">
        填写扩展提示的原 capture_id，核对 device / capture / account
        及浏览器当前账号身份；勿凭标签或猜测邮箱判断。 仅确认原 capture
        和服务端记录确认时的目标当前凭据版本，不永久授权身份不明更新；版本变化需重确认。
        确认后在扩展重传同一候选，服务端重新验证并进行版本条件提交（CAS），扩展
        base 版本不变。
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
          设备 device_id
          <select
            required
            value={deviceId}
            disabled={busy}
            onChange={(e) => {
              setDeviceId(e.target.value);
              setPending(undefined);
            }}
          >
            <option value="">选择有效设备</option>
            {activeDevices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          候选 capture_id
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
          目标 account_id
          <select
            required
            value={accountId}
            disabled={busy}
            onChange={(e) => {
              setAccountId(e.target.value);
              setPending(undefined);
            }}
          >
            <option value="">选择现有账号</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.id}
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
          核对本次候选
        </button>
      </form>
      {pending && (
        <section aria-label="操作确认">
          <h3>
            {pending.kind === "revoke"
              ? "确认撤销设备？"
              : "确认本次候选身份或版本冲突？"}
          </h3>
          {pending.kind === "revoke" ? (
            <p>设备 {pending.id} 将不能继续提交凭据；不会删除已入池账号。</p>
          ) : (
            <p className="mono">
              device_id: {pending.device_id}
              <br />
              capture_id: {pending.capture_id}
              <br />
              account_id: {pending.account_id}
              <br />
              核对浏览器当前账号身份与目标账号当前凭据版本。 仅原 capture
              和服务端记录确认时的目标版本；不永久授权身份不明更新，版本变化需重确认。
            </p>
          )}
          <button disabled={busy} onClick={() => setPending(undefined)}>
            取消操作
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
                    "本次确认已提交，尚未入池。仅适用于原 capture 和服务端记录确认时的目标当前版本，非永久授权。在扩展重传同一候选；服务端重新验证并进行 CAS，扩展 base 版本不变。版本变化或后续身份不明更新需重确认。",
                  );
                }
                setPending(undefined);
              })
            }
          >
            {pending.kind === "revoke" ? "确认撤销" : "仅确认本次候选"}
          </button>
        </section>
      )}
      <footer>
        <button disabled={busy} onClick={onClose}>
          关闭配对窗口
        </button>
      </footer>
    </Modal>
  );
}
