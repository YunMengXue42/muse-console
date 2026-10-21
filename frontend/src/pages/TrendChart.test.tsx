import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TrendChart } from "./TrendChart";

const series = [
  { t: 1792500000, count: 0, errors: 0 },
  { t: 1792501800, count: 6, errors: 1 },
  { t: 1792503600, count: 2, errors: 0 },
];
afterEach(cleanup);

describe("调用趋势", () => {
  it("保持真实桶数、整数刻度与请求错误比例", () => {
    const { container } = render(
      <TrendChart series={series} windowValue="24h" />,
    );
    expect(container.querySelectorAll(".trend-slot").length).toBe(3);
    expect(container.querySelector(".trend-scale")?.textContent).toBe("6420");
    const bars = container.querySelectorAll<HTMLElement>(".trend-request");
    expect(bars[0].style.height).toBe("0%");
    expect(bars[1].style.height).toBe("100%");
    expect(screen.getByText("30 分钟 / 格")).toBeTruthy();
  });

  it("悬停显示当前时段，离开恢复峰值", () => {
    const { container } = render(
      <TrendChart series={series} windowValue="24h" />,
    );
    fireEvent.pointerEnter(container.querySelectorAll(".trend-slot")[1]);
    expect(container.querySelector(".trend-readout")?.textContent).toContain(
      "错误 1",
    );
    expect(container.querySelector("time")?.dateTime).toBe(
      new Date(series[1].t * 1000).toISOString(),
    );
    fireEvent.pointerLeave(screen.getByRole("group"));
    expect(screen.getByText("单格峰值")).toBeTruthy();
  });

  it("方向键可查看零请求桶、首尾与退出", () => {
    const { container } = render(
      <TrendChart series={series} windowValue="7d" />,
    );
    const chart = screen.getByRole("group");
    fireEvent.keyDown(chart, { key: "End" });
    expect(container.querySelector("time")?.dateTime).toBe(
      new Date(series[2].t * 1000).toISOString(),
    );
    fireEvent.keyDown(chart, { key: "Home" });
    expect(container.querySelector(".trend-readout")?.textContent).toContain(
      "请求 0",
    );
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(container.querySelector("time")?.dateTime).toBe(
      new Date(series[0].t * 1000).toISOString(),
    );
    fireEvent.keyDown(chart, { key: "Escape" });
    expect(screen.getByText("单格峰值")).toBeTruthy();
  });

  it("单点与全零数据不产生无效尺寸或重复时间刻度", () => {
    const { container } = render(
      <TrendChart series={[series[0]]} windowValue="1h" />,
    );
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
    expect(container.querySelectorAll(".trend-time-axis span").length).toBe(1);
    expect(container.querySelector(".trend-scale")?.textContent).toBe("10");
  });
});
