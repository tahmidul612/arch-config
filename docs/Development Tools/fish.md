# Fish Shell Setup

{% set fish_content
 %}
{%
  include-markdown "https://raw.githubusercontent.com/tahmidul612/fish-config/refs/heads/main/README.md"

  heading-offset=1
  rewrite-relative-urls=false
%}
{% endset %}

{{ content_tabs(fish_content) }}
