
diamond-config:
  file:
    - managed
    - name: /etc/diamond/diamond.conf
    - source: salt://base/diamond.conf
    - template: jinja
    - require:
        - pkg: diamond

{% if grains['os'] == 'Debian' or grains['os'] == 'Ubuntu' %}
diamond-init-config:
  file:
    - replace
    - name: /etc/default/diamond
    - pattern: DIAMOND_USER=".*"
    - repl: DIAMOND_USER="root"
    - require:
        - pkg: diamond
{% endif %}

diamond-ceph-config:
  file:
    - managed
    - name: /etc/diamond/collectors/CephCollector.conf
    - source: salt://base/CephCollector.conf
    - require:
        - pkg: diamond

diamond-network-config:
  file:
    - managed
    - name: /etc/diamond/collectors/NetworkCollector.conf
    - source: salt://base/NetworkCollector.conf
    - require:
        - pkg: diamond

diamond:
  pkg:
    - installed
    - skip_verify: true
  service:
    - name: diamond
    - running
    - enable: True
    - watch:
      - pkg: diamond
      - file: diamond-network-config
      - file: diamond-ceph-config
      - file: diamond-config
{% if grains['os'] == 'Debian' or grains['os'] == 'Ubuntu' %}
      - file: diamond-init-config
{% endif %}
