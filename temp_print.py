from django.utils import timezone
from core.forms import TransactionForm
from core.models import Transaction

instance = Transaction.objects.first()
form = TransactionForm(instance=instance)
print(form.initial)
