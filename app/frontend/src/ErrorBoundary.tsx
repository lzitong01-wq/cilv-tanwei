import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";

type Props = { children: ReactNode };
type State = { error: Error | null; info: string };

/**
 * 根级错误边界。
 *
 * 为什么需要它：React 19 里没有错误边界的渲染异常会**卸载整棵树**——
 * 页面上任何一处 `data.map(...)` 遇到意外字段就变成整屏空白，用户既看不到内容
 * 也看不到原因。把它兜住之后，单页出错最多坏掉那一页，并且明确告诉用户
 * 「这一页坏了、错误是什么、可以回概览」。
 *
 * 触发过一次的错误会在切页时自动重置，用户不需要刷新浏览器。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 留一条控制台记录，便于现场排查；界面上另有说明
    console.error("页面渲染失败：", error, info.componentStack);
    this.setState({ info: (info.componentStack ?? "").trim().split("\n").slice(0, 4).join("\n") });
  }

  componentDidUpdate(prev: Props) {
    // 路由切走再切回来时重置错误状态
    if (this.state.error && prev.children !== this.props.children) {
      this.setState({ error: null, info: "" });
    }
  }

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="panel" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>这一页出了点问题，内容没能渲染出来</h2>
        <p className="sub">
          错误已记录在浏览器控制台（pageerror）。其他页面的功能不受影响，可以直接回概览继续。
        </p>
        <div className="note mono" style={{ wordBreak: "break-all" }}>
          {error.message || String(error)}
        </div>
        {info && (
          <details style={{ marginTop: 8 }}>
            <summary style={{ cursor: "pointer" }}>组件栈</summary>
            <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: 11, marginTop: 6 }}>
              {info}
            </pre>
          </details>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <Link to="/">
            <button>回到概览</button>
          </Link>
          <button onClick={() => this.setState({ error: null, info: "" })}>重试本页</button>
        </div>
      </div>
    );
  }
}
