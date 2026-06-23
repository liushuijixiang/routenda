import { Building2, Mail, MapPin, Phone, UserRound } from "lucide-react";
import { apiGet, formatDateTime, type Supplier } from "../../../lib/server-api";

export const dynamic = "force-dynamic";

type Site = {
  id: string;
  name: string;
  raw_address: string;
  normalized_address: string;
  geocode_status: string;
  reception_hours: string;
  parking_note: string;
};
type Contact = {
  id: string;
  name: string;
  emails: string[];
  phones: string[];
  status: string;
  language: string;
};
type TimelineEvent = {
  action: string;
  actor: string;
  created_at: string;
  after?: Record<string, unknown>;
};

export default async function SupplierDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [suppliers, sites, contacts, timeline] = await Promise.all([
    apiGet<Supplier[]>("/api/v1/suppliers", []),
    apiGet<Site[]>(`/api/v1/suppliers/${id}/sites`, []),
    apiGet<Contact[]>(`/api/v1/suppliers/${id}/contacts`, []),
    apiGet<TimelineEvent[]>(`/api/v1/suppliers/${id}/timeline`, []),
  ]);
  const supplier = suppliers.find((item) => item.id === id);
  if (!supplier)
    return (
      <main>
        <div className="empty-state">未找到该供应商</div>
      </main>
    );
  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">{supplier.erp_id}</p>
          <h1>{supplier.display_name}</h1>
          <p className="subtitle">{supplier.legal_name}</p>
        </div>
        <span className={`status status-${supplier.status}`}>
          {supplier.status === "active" ? "启用" : supplier.status}
        </span>
      </header>
      <section className="detail-strip">
        <div>
          <Building2 size={17} />
          <span>数据来源</span>
          <strong>{supplier.source_system}</strong>
        </div>
        <div>
          <MapPin size={17} />
          <span>厂区</span>
          <strong>{sites.length}</strong>
        </div>
        <div>
          <UserRound size={17} />
          <span>联系人</span>
          <strong>{contacts.length}</strong>
        </div>
        <div>
          <span>版本</span>
          <strong>v{supplier.version}</strong>
        </div>
      </section>
      <div className="detail-columns">
        <section className="section-band">
          <div className="section-heading">
            <h2>厂区</h2>
            <span>{sites.length}</span>
          </div>
          <div className="record-list">
            {sites.map((site) => (
              <article className="record-row" key={site.id}>
                <div className="record-main">
                  <strong>{site.name}</strong>
                  <p>{site.normalized_address || site.raw_address}</p>
                  <small>
                    接待 {site.reception_hours} · {site.parking_note}
                  </small>
                </div>
                <span className={`status status-${site.geocode_status}`}>
                  {site.geocode_status}
                </span>
              </article>
            ))}
          </div>
        </section>
        <section className="section-band">
          <div className="section-heading">
            <h2>联系人</h2>
            <span>{contacts.length}</span>
          </div>
          <div className="record-list">
            {contacts.map((contact) => (
              <article className="record-row" key={contact.id}>
                <div className="record-main">
                  <strong>{contact.name}</strong>
                  <div className="contact-line">
                    <Mail size={14} />
                    {contact.emails.join("、")}
                  </div>
                  <div className="contact-line">
                    <Phone size={14} />
                    {contact.phones.join("、")}
                  </div>
                </div>
                <span className={`status status-${contact.status}`}>
                  {contact.status}
                </span>
              </article>
            ))}
          </div>
        </section>
      </div>
      <section className="section-band">
        <div className="section-heading">
          <h2>活动时间线</h2>
          <span>{timeline.length}</span>
        </div>
        <div className="timeline">
          {timeline
            .slice()
            .reverse()
            .map((event, index) => (
              <div className="timeline-item" key={`${event.action}-${index}`}>
                <span className="timeline-dot" />
                <div>
                  <strong>{event.action}</strong>
                  <small>
                    {event.actor} · {formatDateTime(event.created_at)}
                  </small>
                </div>
              </div>
            ))}
          {!timeline.length && <div className="empty-state">暂无活动记录</div>}
        </div>
      </section>
    </main>
  );
}
