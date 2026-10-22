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
  it("区分在线、无可用账号、异常、断开与连接中", () => {
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

  it("一行显示状态与可用账号，细节放悬停", () => {
    const { container } = render(
      <InstanceStatus
        status={base}
        unreachable={false}
        lastSyncAt={Date.now()}
      />,
    );
    expect(screen.getByText("在线")).toBeTruthy();
    expect(container.querySelector(".instance-count")?.textContent).toBe("3/4");
    const card = container.querySelector(".instance-card");
    expect(card?.getAttribute("title")).toContain("browser · v1.0.0");
    expect(card?.getAttribute("title")).toContain("刚刚同步");
    const bar = screen.getByRole("progressbar", { name: "可调度账号比例" });
    expect(bar.getAttribute("data-ratio")).toBe("75");
    expect(screen.getByLabelText("账号分布").textContent).toContain("可用3");
    expect(container.querySelector(".instance-tasks")?.textContent).toContain(
      "运行 2",
    );
  });

  it("无可用账号与连接断开有明确状态，切换时闪烁", () => {
    const { rerender, container } = render(
      <InstanceStatus
        status={{ ...base, accounts: { total: 2, available: 0 } }}
        unreachable={false}
        lastSyncAt={null}
      />,
    );
    expect(screen.getByText("无可用账号")).toBeTruthy();
    rerender(<InstanceStatus status={base} unreachable lastSyncAt={null} />);
    expect(screen.getByText("连接断开")).toBeTruthy();
    expect(container.querySelector(".instance-count")).toBeNull();
    expect(container.querySelector(".instance-card.is-flash")).toBeTruthy();
    expect(container.querySelector(".dot.online")).toBeNull();
  });
});
