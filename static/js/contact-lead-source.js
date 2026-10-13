document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-lead-source-fields]').forEach((fields) => {
        const select = fields.querySelector('[name="lead_source"]');
        const other = fields.querySelector('[name="lead_source_other"]');
        const container = fields.querySelector('[data-lead-source-other]');
        const update = () => {
            const isOther = select.value === 'Other';
            container.hidden = !isOther;
            other.disabled = !isOther;
            other.required = isOther;
        };
        select.addEventListener('change', update);
        update();
    });
});
