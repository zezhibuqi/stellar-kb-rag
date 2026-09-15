/**
 * 站点根路由：问答页即首页，服务端直接重定向到 /chat。
 * 登录态校验不在这里做——由 /chat 的 LayoutWrapper 读取本地用户信息后跳 /login。
 */
import { redirect } from "next/navigation";

/** 首页组件：只做重定向，不渲染任何内容。 */
export default function Home() {
  redirect("/chat");
}
