import type { ReactNode } from "react";
import {
  Building2,
  ClipboardPlus,
  LayoutDashboard,
  Settings,
  Stamp,
} from "lucide-react";
import "./style.css";

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <nav aria-label="主导航">
          <a className="brand" href="/dashboard">
            Routenda
          </a>
          <a href="/dashboard">
            <LayoutDashboard size={16} />
            工作台
          </a>
          <a href="/requirements/new">
            <ClipboardPlus size={16} />
            新需求
          </a>
          <a href="/suppliers">
            <Building2 size={16} />
            供应商
          </a>
          <a href="/approvals">
            <Stamp size={16} />
            审批
          </a>
          <a href="/settings/integrations">
            <Settings size={16} />
            集成
          </a>
        </nav>
        {children}
      </body>
    </html>
  );
}
