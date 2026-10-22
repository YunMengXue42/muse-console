import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { InstanceStatus, instanceTone } from "./InstanceStatus";
import type { Status } from "./types";

const base: Status = {
  version: "1.0.0",
  driver: { driver: "browser", ok: true },
  accounts: { total: 4, available: 3 },
  tasks: { pending: 0, running: 2, max_pending: 32 },
};

describe("InstanceStatus", () => {
  it("区分在线、需关注、异常、无响应与连接中", () => {
    expect(instanceTone(base, false)).toBe("online");
    expect(
      instanceTone({ ...base, accounts: { total: 2, available: 0 } }, false),
    ).toBe("warn");
    expect(
      instanceTone(
        { ...base, driver: { driver: "browser", ok: false } },
        false,
      ),
    ).toBe("offline");
    expect(instanceTone(base, true)).toBe("unreachable");
    expect(instanceTone(undefined, false)).toBe("pending");
  });

  it("在线时显示可调度比例、运行任务和同步时间", () => {
    render(
      <InstanceStatus
        status={base}
        unreachable={false}
        lastSyncAt={Date.now()}
      />,
    );
    expect(screen.getByText("驱动在线")).toBeTruthy();
    expect(screen.getByText("2 个任务运行中")).toBeTruthy();
    expect(screen.getByText("刚刚同步")).toBeTruthy();
    const bar = screen.getByRole("progressbar", { name: "可调度账号比例" });
    expect(bar.getAttribute("aria-valuenow")).toBe("3");
    expect(bar.querySelector("i")?.getAttribute("style")).toContain("75%");
  });

  it("无可调度账号与服务无响应给出明确提示", () => {
    const { rerender, container } = render(
      <InstanceStatus
        status={{ ...base, accounts: { total: 2, available: 0 } }}
        unreachable={false}
        lastSyncAt={null}
      />,
    );
    expect(screen.getByText("需要关注")).toBeTruthy();
    expect(screen.getByText("没有可调度账号，新请求会排队")).toBeTruthy();
    rerender(<InstanceStatus status={base} unreachable lastSyncAt={null} />);
    expect(screen.getByText("服务无响应")).toBeTruthy();
    expect(container.querySelector(".instance-card.is-flash")).toBeTruthy();
    expect(container.querySelector(".dot.online")).toBeNull();
  });
});
