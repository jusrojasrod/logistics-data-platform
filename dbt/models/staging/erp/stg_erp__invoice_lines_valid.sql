-- Líneas de factura ERP válidas: filas de stg_erp__invoice_lines_validated
-- sin reglas incumplidas. Una fila por invoice_line_id con sus columnas de
-- negocio, los cinco metadatos y copy_rank. Sin descartar filas por huérfanos
-- ni por origen tardío: solo se excluyen las rechazadas por calidad.

with validated as (

    select * from {{ ref('stg_erp__invoice_lines_validated') }}

)

select
    validated.invoice_line_id,
    validated.invoice_id,
    validated.shipment_id,
    validated.line_number,
    validated.concept,
    validated.tariff_amount,
    validated.billed_revenue_amount,
    validated.tax_amount,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid
