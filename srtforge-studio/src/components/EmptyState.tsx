import type { ReactNode } from "react";

interface Props {
  icon: ReactNode;
  title: string;
  body: string;
  cta?: { label: string; onClick: () => void };
}

export const EmptyState = ({ icon, title, body, cta }: Props) => (
  <div className="empty">
    <div className="empty-icon">{icon}</div>
    <h3>{title}</h3>
    <p>{body}</p>
    {cta && (
      <button className="btn btn-primary" onClick={cta.onClick}>
        {cta.label}
      </button>
    )}
  </div>
);
