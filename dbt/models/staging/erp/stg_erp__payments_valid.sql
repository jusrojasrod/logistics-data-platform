-- Pagos ERP válidos: filas de stg_erp__payments_validated sin reglas
-- incumplidas. Una fila por payment_id con sus columnas de negocio, los cinco
-- metadatos y copy_rank. Sin descartar filas por huérfanos ni por origen
-- tardío: solo se excluyen las rechazadas por calidad.

with validated as (

    select * from {{ ref('stg_erp__payments_validated') }}

)

select
    validated.payment_id,
    validated.invoice_id,
    validated.customer_id,
    validated.paid_date,
    validated.payment_amount,
    validated.payment_method,
    validated.currency_code,
    validated.run_id,
    validated.source_system,
    validated.source_file,
    validated.source_file_hash,
    validated.ingested_at,
    validated.copy_rank
from validated
where validated.is_valid
