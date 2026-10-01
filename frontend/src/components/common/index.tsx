import React from 'react';

export function Card({ title, children, className = "", onClick }: { title?: React.ReactNode, children: React.ReactNode, className?: string, onClick?: () => void }) {
  return (
    <div className={`card ${className} ${onClick ? 'clickable' : ''}`} onClick={onClick}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  );
}

export function Badge({ children, variant = "neutral" }: { children: React.ReactNode, variant?: "primary" | "success" | "warning" | "danger" | "neutral" }) {
  return <span className={`badge ${variant}`}>{children}</span>;
}

export function Button({ children, onClick, variant = "neutral", disabled = false, className = "" }: { children: React.ReactNode, onClick?: () => void, variant?: "primary" | "neutral", disabled?: boolean, className?: string }) {
  return (
    <button className={`button ${variant} ${className}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
}

export function OccupancyBadge({ state }: { state: string }) {
  let variant: any = "neutral";
  if (state === "EMPTY") variant = "neutral";
  if (state === "LOW") variant = "success";
  if (state === "MEDIUM") variant = "warning";
  if (state === "HIGH") variant = "danger";
  
  return <Badge variant={variant}>{state}</Badge>;
}

export function RangeBadge({ status }: { status: string }) {
  return <Badge variant={status === "WITHIN_RANGE" ? "success" : "warning"}>{status.replace("_", " ")}</Badge>;
}

export function SectionHeader({ eyebrow, title, description, action }: {
  eyebrow?: string; title: string; description?: string; action?: React.ReactNode;
}) {
  return <header className="section-header">
    <div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description && <p>{description}</p>}</div>
    {action && <div className="section-header-action">{action}</div>}
  </header>;
}

export function EmptyState({ title, children, action }: {
  title: string; children?: React.ReactNode; action?: React.ReactNode;
}) {
  return <div className="product-empty-state"><span aria-hidden="true">✳</span><strong>{title}</strong>
    {children && <p>{children}</p>}{action}</div>;
}

export function ErrorState({ title, children, onRetry, details }: {
  title: string; children?: React.ReactNode; onRetry?: () => void; details?: React.ReactNode;
}) {
  return <div className="product-error-state" role="alert"><strong>{title}</strong>
    {children && <p>{children}</p>}{details && <details><summary>Technical details</summary><div>{details}</div></details>}
    {onRetry && <Button onClick={onRetry}>Try again</Button>}</div>;
}
