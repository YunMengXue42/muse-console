import { useEffect, useRef, type ReactNode } from "react";
/** 模态框锁定背景焦点，支持 Escape 并将焦点归还触发控件。 */
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    return () => {
      document.body.style.overflow = old;
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="veil"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`modal ${wide ? "wide" : ""}`}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            onClose();
          }
          if (e.key === "Tab") {
            const items = ref.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]',
            );
            if (!items?.length) {
              e.preventDefault();
              return;
            }
            const first = items[0],
              last = items[items.length - 1];
            if (
              e.shiftKey &&
              (document.activeElement === first ||
                document.activeElement === ref.current)
            ) {
              e.preventDefault();
              last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first.focus();
            }
          }
        }}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
/** 未收到真实数据时明确显示空状态。 */
export function Empty({
  text = "暂无记录",
  children,
}: {
  text?: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-mark">—</span>
      <h3>{text}</h3>
      <p>接入账号或发起 API 调用后，真实数据将在此显示。</p>
      {children}
    </div>
  );
}
/** 用小型几何图标区分导航，而不引入运行时图标依赖。 */
export function Icon({ index }: { index: number }) {
  return (
    <svg
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      {index === 0 ? (
        <>
          <rect x="3" y="4" width="18" height="16" rx="4" />
          <circle cx="9" cy="10" r="2" />
          <path d="M5 17c0-4 8-4 8 0m3-8h3m-3 4h3" />
        </>
      ) : index === 1 ? (
        <>
          <path d="M4 4v16h17M8 15l4-5 4 3 5-8" />
        </>
      ) : index === 2 ? (
        <>
          <rect x="5" y="4" width="14" height="17" rx="3" />
          <path d="M9 3h6M9 10h6m-6 5h6" />
        </>
      ) : index === 3 ? (
        <>
          <path d="M7 3h10l3 3v15H4V3h3m1 6h8m-8 4h8m-8 4h5" />
        </>
      ) : (
        <>
          <circle cx="8" cy="10" r="5" />
          <path d="m12 14 8 7m-4-10 2-2m-5 5 3-3" />
        </>
      )}
    </svg>
  );
}
