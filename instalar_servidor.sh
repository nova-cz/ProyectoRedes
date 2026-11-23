#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
#  NOVA MOTORS · Instalador del servidor de planta (AlmaLinux 9)
#  Proyecto ADR 2026
#
#  Uso:
#     sudo bash instalar_servidor.sh            # instala todo
#     sudo bash instalar_servidor.sh base       # solo paquetes base + Python
#     sudo bash instalar_servidor.sh nagios     # solo Nagios
#     sudo bash instalar_servidor.sh cacti      # solo Cacti
# ─────────────────────────────────────────────────────────────────────────────
set -u

if [ "$EUID" -ne 0 ]; then
    echo "Ejecuta este instalador con sudo:  sudo bash $0"
    exit 1
fi

paso()  { echo -e "\n\e[1;38;5;39m══▶ $*\e[0m"; }
bien()  { echo -e "  \e[1;32m✔\e[0m $*"; }
alerta(){ echo -e "  \e[1;33m!\e[0m $*"; }

abrir_http() {
    if systemctl is-active --quiet firewalld; then
        firewall-cmd --permanent --add-service=http >/dev/null && firewall-cmd --reload >/dev/null
        bien "Puerto HTTP (80) abierto en el firewall"
    fi
}

instalar_base() {
    paso "Habilitando repositorios EPEL y CRB"
    dnf install -y epel-release
    dnf config-manager --set-enabled crb 2>/dev/null || true

    paso "Instalando herramientas de administración, respaldo y monitoreo"
    dnf install -y python3 python3-pip cronie at rsync tar gzip bzip2 xfsdump psmisc \
                   nmap iftop htop wireshark wireshark-cli util-linux
    dnf install -y xhost 2>/dev/null || dnf install -y xorg-x11-server-utils 2>/dev/null || true
    dnf install -y dump 2>/dev/null \
        || alerta "El paquete 'dump' (para ext4) no está disponible; en XFS se usa xfsdump (ya instalado)."

    paso "Instalando librerías de Python para el menú (rich, questionary)"
    pip3 install --upgrade rich questionary

    paso "Activando servicios cron y at"
    systemctl enable --now crond atd

    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        usermod -aG wireshark "$SUDO_USER" && bien "$SUDO_USER agregado al grupo wireshark"
    fi
    bien "Base instalada"
}

instalar_nagios() {
    paso "Instalando Nagios Core + Apache + plugins"
    dnf install -y epel-release
    dnf install -y httpd php nagios nagios-common nagios-plugins-all

    echo
    read -r -s -p "  Contraseña para el usuario web 'nagiosadmin' [Enter = nova2026]: " NPASS
    echo
    NPASS=${NPASS:-nova2026}
    htpasswd -b -c /etc/nagios/passwd nagiosadmin "$NPASS" >/dev/null
    chown root:apache /etc/nagios/passwd
    chmod 640 /etc/nagios/passwd

    # SELinux: permitir que Apache ejecute los CGI de Nagios
    if command -v setsebool >/dev/null; then
        setsebool -P httpd_can_network_connect on 2>/dev/null || true
    fi

    nagios -v /etc/nagios/nagios.cfg >/dev/null && bien "Configuración de Nagios válida"
    systemctl enable --now httpd nagios
    abrir_http
    bien "Nagios listo en  http://localhost/nagios  (usuario: nagiosadmin)"
}

instalar_cacti() {
    paso "Instalando Cacti + MariaDB + SNMP"
    dnf install -y epel-release
    dnf install -y httpd php php-mysqlnd php-snmp php-gd php-ldap php-mbstring php-xml \
                   php-intl php-gmp php-posix mariadb-server net-snmp net-snmp-utils rrdtool cacti

    paso "Ajustando MariaDB para Cacti"
    cat > /etc/my.cnf.d/cacti.cnf <<'EOF'
[mysqld]
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
max_heap_table_size = 128M
tmp_table_size = 128M
join_buffer_size = 64M
innodb_buffer_pool_size = 512M
innodb_doublewrite = OFF
innodb_flush_log_at_timeout = 3
innodb_read_io_threads = 32
innodb_write_io_threads = 16
innodb_io_capacity = 5000
innodb_io_capacity_max = 10000
EOF
    systemctl enable --now mariadb snmpd
    systemctl restart mariadb

    paso "Creando base de datos 'cacti'"
    CPASS="Cacti$(date +%s | tail -c 6)"
    mysql -u root <<SQL
CREATE DATABASE IF NOT EXISTS cacti CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'cactiuser'@'localhost' IDENTIFIED BY '${CPASS}';
ALTER USER 'cactiuser'@'localhost' IDENTIFIED BY '${CPASS}';
GRANT ALL PRIVILEGES ON cacti.* TO 'cactiuser'@'localhost';
GRANT SELECT ON mysql.time_zone_name TO 'cactiuser'@'localhost';
FLUSH PRIVILEGES;
SQL
    mysql_tzinfo_to_sql /usr/share/zoneinfo 2>/dev/null | mysql -u root mysql

    TABLAS=$(mysql -u root -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='cacti'")
    if [ "$TABLAS" = "0" ]; then
        SQLF=$(rpm -ql cacti | grep -E '/cacti\.sql$' | head -n 1)
        mysql -u root cacti < "$SQLF" && bien "Esquema importado desde $SQLF"
    fi

    paso "Configurando conexión de Cacti a la base de datos"
    for CONF in /etc/cacti/db.php /usr/share/cacti/include/config.php; do
        if [ -f "$CONF" ] && grep -q 'database_username' "$CONF"; then
            sed -i "s/^\$database_username.*/\$database_username = 'cactiuser';/" "$CONF"
            sed -i "s/^\$database_password.*/\$database_password = '${CPASS}';/" "$CONF"
            bien "Credenciales escritas en $CONF"
            break
        fi
    done

    paso "Ajustando PHP (zona horaria, memoria)"
    sed -i 's|^;\?date.timezone.*|date.timezone = America/Mexico_City|' /etc/php.ini
    sed -i 's|^memory_limit.*|memory_limit = 512M|' /etc/php.ini
    sed -i 's|^max_execution_time.*|max_execution_time = 60|' /etc/php.ini

    # Activar el poller (recolector de datos) cada 5 minutos
    [ -f /etc/cron.d/cacti ] && sed -i 's/^#\(.*poller\.php.*\)/\1/' /etc/cron.d/cacti

    if command -v setsebool >/dev/null; then
        setsebool -P httpd_can_network_connect on 2>/dev/null || true
        setsebool -P httpd_can_network_connect_db on 2>/dev/null || true
    fi

    systemctl enable --now httpd php-fpm 2>/dev/null || systemctl enable --now httpd
    systemctl restart httpd php-fpm 2>/dev/null || systemctl restart httpd
    abrir_http
    bien "Cacti listo en  http://localhost/cacti  (primer acceso: admin / admin, luego sigue el asistente)"
    echo "  Contraseña de la BD (cactiuser): ${CPASS}" | tee /root/cacti_bd.txt
}

case "${1:-todo}" in
    base)   instalar_base ;;
    nagios) instalar_nagios ;;
    cacti)  instalar_cacti ;;
    todo)   instalar_base; instalar_nagios; instalar_cacti ;;
    *)      echo "Opciones: base | nagios | cacti | todo"; exit 1 ;;
esac

echo -e "\n\e[1;32m════ Instalación terminada ════\e[0m"
echo "Ejecuta el menú con:   sudo python3 nova_admin.py"
