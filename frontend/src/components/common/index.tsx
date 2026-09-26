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
