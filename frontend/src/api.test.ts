import { describe, it, expect, vi, afterEach } from "vitest";
import { Api, ApiError, framePoint } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("管理 API", () => {
  it("同源 Bearer 请求与 JSON 变更载荷", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ data: [] })));
    vi.stubGlobal("fetch", fetcher);
    await new Api("test-only").request("/accounts/import", "POST", {
      content: "[]",
      preview: true,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/admin/accounts/import",
      expect.objectContaining({
        method: "POST",
        headers: {
          Authorization: "Bearer test-only",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ content: "[]", preview: true }),
        cache: "no-store",
      }),
    );
  });
  it("401 触发退出并保留服务器错误", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { message: "expired" } }), {
          status: 401,
        }),
      ),
    );
    const logout = vi.fn();
    await expect(
      new Api("bad", logout).request("/status"),
    ).rejects.toMatchObject({ status: 401, message: "expired" });
    expect(logout).toHaveBeenCalledOnce();
  });
  it("501 不伪装成登录成功", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("{}", { status: 501 })),
    );
    await expect(
      new Api("test").request("/login-sessions", "POST", {}),
    ).rejects.toBeInstanceOf(ApiError);
  });
  it("非 JSON 网关错误也包含状态码", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Bad Gateway", { status: 502 })),
    );
    await expect(new Api("test").request("/status")).rejects.toMatchObject({
      status: 502,
    });
  });
  it("截图点击映射并限制在视口内", () => {
    expect(
      framePoint(
        330,
        245,
        { left: 10, top: 20, width: 640, height: 450 },
        1280,
        900,
      ),
    ).toEqual({ x: 640, y: 450 });
    expect(
      framePoint(
        900,
        1000,
        { left: 0, top: 0, width: 640, height: 450 },
        1280,
        900,
      ),
    ).toEqual({ x: 1279, y: 899 });
  });
});
